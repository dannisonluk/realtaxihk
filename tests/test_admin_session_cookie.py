"""The admin session cookie: issuance, rotation, CSRF and logout.

These tests pin the fix for **SEV-1** — an admin session that died at the
15-minute access-token expiry with no way to recover. They are integration tests
against the real routes and a real database, because every part of the mechanism
that could go wrong lives at a boundary a unit test would stub away: `Set-Cookie`
attributes are produced by Starlette's `Response`, the cookie is read back by
Starlette's `Request`, and the CSRF comparison spans both.

What each group is for
----------------------
* **Issuance** — the three endpoints that complete the second factor must each
  set the pair. The failure mode being guarded against is "added to two of
  three", which produces a session that works until it silently does not.
* **Attributes** — `HttpOnly` is the reason the refresh token is in a cookie at
  all. If it regresses to readable, the design is strictly worse than a bearer
  token, because a cookie is also sent automatically. This is asserted on the
  raw `Set-Cookie` header, not on a parsed object, so a framework change cannot
  quietly drop it.
* **Rotation** — single-use, and a replay revokes the family. This is the
  detection mechanism: without it a stolen cookie is usable indefinitely and
  invisibly.
* **CSRF** — the cookie is auto-attached, so a request without the header must
  be refused. This is the half that makes the cookie safe.
* **Logout** — must work with an *expired* access token, must clear the cookie,
  and must revoke server-side rather than only client-side.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.core.passwords import hash_password
from app.core.totp import _decode_key, _hotp, current_step, generate_secret
from app.services.admin_refresh_service import (
    CSRF_COOKIE,
    REFRESH_COOKIE,
    csrf_cookie_name,
    refresh_cookie_name,
)

USERNAME = "cookie.admin"
EMAIL = "cookie.admin@example.com"
PASSWORD = "correct-horse-battery-staple-77"

BASE = "/api/v1/admin/auth"


# ---------------------------------------------------------------- #
# Seeding and login helpers
# ---------------------------------------------------------------- #


def _seed_admin(client, *, username: str = USERNAME, email: str = EMAIL) -> uuid.UUID:
    """Insert an enrolled admin directly, returning the id.

    A direct INSERT rather than the provisioning CLI: the tests need a known
    TOTP secret they can generate codes from, and going through the script would
    couple every test here to its argument parsing.
    """
    admin_id = uuid.uuid4()
    secret = generate_secret()

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await session.execute(
                    text(
                        "INSERT INTO admin_accounts "
                        "(id, username, email, full_name, password_hash, totp_secret, "
                        " totp_enrolled_at, is_active, failed_login_count, created_at, updated_at) "
                        "VALUES (:id, :username, :email, 'Cookie Admin', :pw, :secret, "
                        " now(), true, 0, now(), now())"
                    ),
                    {
                        "id": admin_id,
                        "username": username,
                        "email": email,
                        "pw": hash_password(PASSWORD),
                        "secret": secret,
                    },
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_inner())
    return admin_id


def _totp_now(secret: str) -> str:
    """The code the server will accept right now.

    Uses the module's own `_hotp`/`current_step` rather than a reimplementation:
    the RFC vectors in `tests/test_totp.py` are what prove `_hotp` is correct, so
    re-deriving it here would only duplicate the risk.
    """
    return _hotp(_decode_key(secret), current_step(), 6)


def _secret_for(client, username: str = USERNAME) -> str:
    """Read back the seeded secret so the test can generate a valid code."""

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                result = await session.execute(
                    text("SELECT totp_secret FROM admin_accounts WHERE username = :u"),
                    {"u": username},
                )
                return result.scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(_inner())


def _sign_in(client, *, username: str = USERNAME, password: str = PASSWORD):
    """Complete the three-step login and return the **response**.

    The client's cookie jar retains the session cookies across these calls,
    which is the browser behaviour being modelled. The response is returned
    rather than just the token because several tests assert on its `Set-Cookie`
    headers, and `TestClient` does not keep the last response for inspection.
    """
    secret = _secret_for(client, username)
    challenge = client.post(
        f"{BASE}/login", json={"username": username, "password": password}
    ).json()["challenge_token"]
    response = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": challenge, "code": _totp_now(secret)},
    )
    assert response.status_code == 200, response.text
    return response


def _sign_in_token(client, *, username: str = USERNAME) -> str:
    """`_sign_in` for the tests that only need the access token."""
    return _sign_in(client, username=username).json()["access_token"]


def _set_active(client, *, active: bool, username: str = USERNAME) -> None:
    """Flip `admin_accounts.is_active`, to model a deactivation mid-session.

    Written directly rather than through an admin API: there is no "disable
    yourself" endpoint, and the point of the test is what happens on the *next*
    request, not how the flag was flipped.
    """

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await session.execute(
                    text("UPDATE admin_accounts SET is_active = :a WHERE username = :u"),
                    {"a": active, "u": username},
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_inner())


def _jar_cookie(client, name: str) -> str | None:
    """A cookie value from the client's jar, trying both prefixed spellings."""
    value = client.cookies.get(name)
    return value if value else None


def _refresh_from_jar(client) -> str | None:
    """The refresh cookie's raw value, regardless of the `__Host-` prefix.

    The prefix is environment-dependent (`__Host-` requires `Secure`, which
    requires https), so the tests read it through the same helper the server
    uses rather than hardcoding a name that would only be right in one env.
    """
    for secure in (False, True):
        value = _jar_cookie(client, refresh_cookie_name(secure=secure))
        if value:
            return value
    return None


def _csrf_from_jar(client) -> str | None:
    for secure in (False, True):
        value = _jar_cookie(client, csrf_cookie_name(secure=secure))
        if value:
            return value
    return None


def _raw_set_cookie(response, name: str) -> str | None:
    """The full `Set-Cookie` header for one cookie, or None.

    Read from the raw header list rather than a parsed jar, because the
    attributes (`HttpOnly`, `SameSite`, `Path`) are the thing under test and a
    parsed object would have already applied them.
    """
    for header in response.headers.get_list("set-cookie"):
        if header.split("=", 1)[0].strip() == name:
            return header
    return None


def _any_refresh_set_cookie(response) -> str | None:
    return _raw_set_cookie(response, refresh_cookie_name(secure=False)) or _raw_set_cookie(
        response, refresh_cookie_name(secure=True)
    )


def _any_csrf_set_cookie(response) -> str | None:
    return _raw_set_cookie(response, csrf_cookie_name(secure=False)) or _raw_set_cookie(
        response, csrf_cookie_name(secure=True)
    )


# ---------------------------------------------------------------- #
# Issuance
# ---------------------------------------------------------------- #


def test_totp_verify_sets_the_refresh_and_csrf_cookies(client):
    """Step 2a must plant the pair, or the session has a 15-minute life again."""
    _seed_admin(client)
    response = _sign_in(client)

    assert _any_refresh_set_cookie(response), "no refresh cookie was issued"
    assert _any_csrf_set_cookie(response), "no CSRF cookie was issued"


def test_no_refresh_token_is_returned_in_the_body(client):
    """The credential must not be reachable by JavaScript.

    This is the assertion that makes the whole design meaningful: if the refresh
    token is in the response body, it is in `sessionStorage` a moment later, and
    the `HttpOnly` cookie is decoration.
    """
    _seed_admin(client)
    body = _sign_in(client).json()

    assert set(body.keys()) == {"access_token", "token_type", "admin"}
    assert "refresh_token" not in body


def test_enrolment_confirm_sets_the_cookies_too(client):
    """The first-login path is a session-issuing path like any other.

    The failure mode this guards is "added to two of the three endpoints": an
    admin who enrols on their first login would then have the 15-minute session
    the fix was for, while every other admin got a working one.
    """
    admin_id = uuid.uuid4()
    secret = generate_secret()

    async def _seed_unenrolled():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await session.execute(
                    text(
                        "INSERT INTO admin_accounts "
                        "(id, username, email, full_name, password_hash, totp_secret, "
                        " totp_enrolled_at, is_active, failed_login_count, created_at, updated_at) "
                        "VALUES (:id, :u, :e, 'New Admin', :pw, NULL, NULL, true, 0, now(), now())"
                    ),
                    {
                        "id": admin_id,
                        "u": "fresh.admin",
                        "e": "fresh.admin@example.com",
                        "pw": hash_password(PASSWORD),
                    },
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed_unenrolled())

    # `/login` returns the enrolment material and a challenge; the secret is the
    # one now in Redis under `admin:enrol:<id>`, which is what the caller uses.
    login = client.post(
        f"{BASE}/login", json={"username": "fresh.admin", "password": PASSWORD}
    ).json()
    assert login["next"] == "enrolment_required"
    pending_secret = login["enrolment"]["secret"]

    response = client.post(
        f"{BASE}/totp/enrol/confirm",
        json={"challenge_token": login["challenge_token"], "code": _totp_now(pending_secret)},
    )
    assert response.status_code == 200, response.text
    assert _any_refresh_set_cookie(response), "enrolment confirm did not issue the cookie"
    assert "refresh_token" not in response.json()
    # `secret` is unused after the assert above; keep the binding meaningful.
    assert secret


# ---------------------------------------------------------------- #
# Cookie attributes
# ---------------------------------------------------------------- #


def test_refresh_cookie_is_httponly_and_scoped(client):
    """`HttpOnly` and a narrow `Path` are the two properties that matter most.

    `HttpOnly` is what stops script reading the value. `Path`
    (`/api/v1/admin/auth`) means the credential is not attached to every other
    console request, which shrinks where it can leak to.
    """
    _seed_admin(client)
    raw = _any_refresh_set_cookie(_sign_in(client))

    assert raw, "no refresh cookie was set"
    lowered = raw.lower()
    assert "httponly" in lowered
    assert "path=/api/v1/admin/auth" in lowered
    # `SameSite` must be present in one of its two forms: `Strict` in prod,
    # `Lax` in dev. Absent would mean the browser default, which is not a
    # decision anybody made.
    assert "samesite=strict" in lowered or "samesite=lax" in lowered


def test_csrf_cookie_is_readable_by_script(client):
    """The opposite of the refresh cookie, and deliberately so.

    The double-submit pattern needs the console's own script to read this value
    and echo it, which an `HttpOnly` cookie would forbid. Its confidentiality is
    not what protects it — an attacker who can read it is already same-origin.
    """
    _seed_admin(client)
    raw = _any_csrf_set_cookie(_sign_in(client))

    assert raw, "no CSRF cookie was set"
    assert "httponly" not in raw.lower()


# ---------------------------------------------------------------- #
# Rotation
# ---------------------------------------------------------------- #


def test_refresh_rotates_and_returns_a_new_access_token(client):
    """The core of the fix: an expiring access token can be renewed."""
    _seed_admin(client)
    _sign_in(client)

    response = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": _csrf_from_jar(client)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert "refresh_token" not in body


def test_refresh_without_the_csrf_header_is_refused(client):
    """A bare cookie is not sufficient — that is the entire CSRF defence.

    An attacker's page can cause the browser to *send* the refresh cookie; it
    cannot read the CSRF value to forge this header.
    """
    _seed_admin(client)
    _sign_in(client)

    response = client.post(f"{BASE}/refresh")
    assert response.status_code == 401, response.text


def test_refresh_with_a_wrong_csrf_header_is_refused(client):
    """A guessed or stale CSRF value is refused, and does not rotate anything."""
    _seed_admin(client)
    _sign_in(client)

    response = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": "not-the-token"})
    assert response.status_code == 401, response.text


def test_refresh_without_a_cookie_is_a_clean_401(client):
    """No session at all is a 401, not a 500 and not a redirect."""
    response = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": "anything"})
    assert response.status_code == 401, response.text


def test_a_replayed_refresh_cookie_revokes_the_family(client):
    """Presenting an already-rotated token means theft: kill every session.

    This is what makes a leaked cookie *detectable*. Without it an attacker who
    lifted the cookie could rotate silently alongside the real operator, and
    neither party would ever know.
    """
    _seed_admin(client)
    _sign_in(client)

    csrf = _csrf_from_jar(client)
    csrf_name = csrf_cookie_name(secure=False)
    original = client.cookies.get(refresh_cookie_name(secure=False)) or client.cookies.get(
        refresh_cookie_name(secure=True)
    )
    assert original, "sign-in did not leave a refresh cookie in the jar"

    # First rotation succeeds, and `original` is now revoked.
    first = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200, first.text

    # Replay the revoked original. The jar now holds the rotated cookie, so the
    # old one is pinned explicitly — this is what an attacker with a stolen copy
    # would send.
    old_name = refresh_cookie_name(secure=False)
    replayed = client.post(
        f"{BASE}/refresh",
        headers={
            "X-CSRF-Token": csrf,
            "Cookie": f"{old_name}={original}; {csrf_name}={csrf}",
        },
    )
    assert replayed.status_code == 401, replayed.text

    # The family is dead: even the token the first rotation handed out is now
    # revoked. That is the point — after a replay we cannot tell the operator
    # from the attacker, so neither keeps a session.
    after = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": csrf})
    assert after.status_code == 401, after.text


def test_rotation_issues_a_fresh_csrf_token(client):
    """The CSRF token rotates with the refresh token, not independently.

    A CSRF value that survived a rotation would be a value that has been around
    longer than the credential it protects, which is the wrong way round.
    """
    _seed_admin(client)
    _sign_in(client)
    before = _csrf_from_jar(client)

    first = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": before})
    assert first.status_code == 200, first.text
    after = _csrf_from_jar(client)

    assert after and after != before


# ---------------------------------------------------------------- #
# Logout
# ---------------------------------------------------------------- #


def test_logout_clears_the_cookie(client):
    """Signing out must actually end the session in the browser."""
    _seed_admin(client)
    _sign_in(client)

    response = client.post(f"{BASE}/logout", headers={"X-CSRF-Token": _csrf_from_jar(client)})
    assert response.status_code == 200, response.text
    assert _refresh_from_jar(client) is None


def test_logout_without_an_access_token_still_works(client):
    """The whole reason logout is not bearer-gated.

    The common case is an operator whose 15-minute access token has already
    expired. If logout required a valid token, that operator could not clear a
    live refresh cookie from a shared machine — which is precisely when they
    most need to.
    """
    _seed_admin(client)
    _sign_in(client)

    response = client.post(
        f"{BASE}/logout",
        headers={
            "X-CSRF-Token": _csrf_from_jar(client),
            "Authorization": "Bearer expired.garbage.token",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


def test_logout_revokes_server_side_not_just_the_cookie(client):
    """A cleared cookie is not a revoked session.

    Copying the cookie value before logout and replaying it afterwards must
    fail — otherwise the browser is clean and the credential still works.
    """
    _seed_admin(client)
    _sign_in(client)
    csrf = _csrf_from_jar(client)
    stolen = _refresh_from_jar(client)
    assert stolen

    revoke = client.post(f"{BASE}/logout", headers={"X-CSRF-Token": csrf})
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["revoked"] is True

    replayed = client.post(
        f"{BASE}/refresh",
        headers={
            "X-CSRF-Token": csrf,
            "Cookie": f"{refresh_cookie_name(secure=False)}={stolen}",
        },
    )
    assert replayed.status_code == 401, "revoked cookie still refreshes — logout is cosmetic"


def test_logout_without_the_csrf_header_does_not_revoke(client):
    """A forged cross-site logout must not be able to end a live session.

    The cookie is still cleared locally — the operator asked for that — but the
    server-side family survives, so an attacker's page cannot log the operator
    out as a denial-of-service.
    """
    _seed_admin(client)
    _sign_in(client)
    csrf = _csrf_from_jar(client)
    live = _refresh_from_jar(client)

    response = client.post(f"{BASE}/logout")
    assert response.status_code == 200, response.text
    assert response.json()["revoked"] is False

    # The forged request dropped this browser's cookie, but the credential is
    # still valid — proven by using the saved value directly.
    still_valid = client.post(
        f"{BASE}/refresh",
        headers={
            "X-CSRF-Token": csrf,
            "Cookie": f"{refresh_cookie_name(secure=False)}={live}",
        },
    )
    assert still_valid.status_code == 200, "a forged logout revoked a live session"


# ---------------------------------------------------------------- #
# The access-token path is unchanged
# ---------------------------------------------------------------- #


def test_the_access_token_still_authenticates_admin_routes(client):
    """Adding a cookie must not have moved the bearer-token contract."""
    _seed_admin(client)
    token = _sign_in_token(client)

    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text


def test_refresh_issues_a_token_that_authenticates_admin_routes(client):
    """The renewed token must be a first-class admin token, not a lesser one.

    A refresh path that produced a token `require_admin` rejected would be worse
    than no refresh path at all: the session would appear to extend and then
    fail on every request.
    """
    _seed_admin(client)
    _sign_in(client)
    renewed = client.post(
        f"{BASE}/refresh", headers={"X-CSRF-Token": _csrf_from_jar(client)}
    ).json()["access_token"]

    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {renewed}"})
    assert response.status_code == 200, response.text


def test_cookie_names_match_what_the_console_reads(client):
    """The console hardcodes these strings in `session.ts`; pin them here.

    It cannot import Python, so a rename on this side would otherwise be caught
    only by an operator whose session silently stopped refreshing.
    """
    assert refresh_cookie_name(secure=False) == REFRESH_COOKIE == "realtaxi_admin_refresh"
    assert csrf_cookie_name(secure=False) == CSRF_COOKIE == "realtaxi_admin_csrf"


# ---------------------------------------------------------------- #
# Live account state — the guard the route-table audit cannot see
# ---------------------------------------------------------------- #


def test_refresh_refuses_a_deactivated_admin(client):
    """A disabled admin must not be able to renew their session.

    This is the live-state guarantee `tests/test_security_hardening.py` asserts
    *structurally* for every other route (via its declared-dependency scan). It
    cannot see it here, because the refresh route authenticates with a cookie
    and does its lookup inside the handler, alongside the rotation it shares a
    transaction with. So the property is pinned behaviourally instead — and
    without this test the dependency would be a declaration with nothing behind
    it, which is exactly the failure mode the audit exists to prevent.

    Deactivation is the case that matters: the cookie is still cryptographically
    valid and unexpired, so the *only* thing that can refuse it is reading the
    live row.
    """
    _seed_admin(client)
    _sign_in(client)

    # The session works while the account is active — otherwise a 403 below
    # would prove nothing about the guard.
    assert (
        client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": _csrf_from_jar(client)}).status_code
        == 200
    )

    _set_active(client, active=False)

    refused = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": _csrf_from_jar(client)})
    assert refused.status_code == 403, refused.text
    # The project's error envelope, not FastAPI's `{detail}` — `app/core/exceptions.py`
    # normalises an `HTTPException(detail=...)` to `{code, message, details}`.
    assert refused.json()["message"] == "account disabled"


def test_refresh_clears_the_cookies_when_the_account_is_disabled(client):
    """The dead cookie must be removed, not left for the console to retry.

    A refused refresh that kept the cookie would have the console loop: every
    boot, one 403 and one wasted round-trip, forever. Clearing it makes the
    refusal terminal — the next request simply has no session.

    Asserted through the cookie jar, matching `test_logout_clears_the_cookie`:
    the jar applies `Max-Age=0` the way a browser would, so an empty jar is
    evidence the cookie is really gone rather than merely a header that looks
    right.
    """
    _seed_admin(client)
    _sign_in(client)
    assert _refresh_from_jar(client) is not None, "sign-in did not plant a cookie to clear"

    _set_active(client, active=False)

    refused = client.post(f"{BASE}/refresh", headers={"X-CSRF-Token": _csrf_from_jar(client)})
    assert refused.status_code == 403, refused.text

    assert _refresh_from_jar(client) is None, "the disabled-account refusal left the cookie behind"
    assert _csrf_from_jar(client) is None, "the CSRF cookie outlived the refresh cookie"


def test_logout_still_works_for_a_disabled_admin(client):
    """Logout must not become unreachable when an account is disabled.

    The guard on logout is deliberately weaker than the one on refresh: logout
    only *destroys* a credential, so an operator whose account was disabled must
    still be able to clear their own cookie. Refusing them would strand a live
    cookie on a shared machine — the precise outcome logout exists to prevent.
    """
    _seed_admin(client)
    _sign_in(client)
    _set_active(client, active=False)

    response = client.post(f"{BASE}/logout", headers={"X-CSRF-Token": _csrf_from_jar(client)})
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "revoked": True}

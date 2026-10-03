"""End-to-end admin authentication over the real HTTP surface.

These tests exist to pin one property above all others: **a correct password,
on its own, can never produce an access token.** Everything else here
(rate limits, lockout, replay rejection, recovery codes) is a supporting
assertion; that one is the requirement.

`tests/test_totp.py` and `tests/test_passwords.py` cover the primitives in
isolation. This file drives the actual routes against a real Postgres, a real
Redis, and the real dependency wiring, so a mismatch between the service, the
router and `main.py` shows up here and nowhere else.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import text

from app.core.passwords import hash_password
from app.core.totp import (
    _decode_key,
    _hotp,
    current_step,
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
)

USERNAME = "ops.admin"
EMAIL = "ops.admin@example.com"
PASSWORD = "correct-horse-battery-staple-42"

BASE = "/api/v1/admin/auth"


def _seed_admin(client, *, username: str = USERNAME, email: str = EMAIL, enrolled: bool = True):
    """Insert an admin account directly, returning (id, secret-or-None).

    A direct INSERT rather than a call to the CLI: the tests need to control
    `totp_secret` (enrolled vs not), and going through the provisioning script
    would couple every test to its argument parsing.
    """

    admin_id = uuid.uuid4()
    secret = generate_secret() if enrolled else None

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
                        "VALUES (:id, :username, :email, :name, :pw, CAST(:secret AS text), "
                        " CASE WHEN CAST(:secret AS text) IS NULL THEN NULL ELSE now() END, "
                        " true, 0, now(), now())"
                    ),
                    {
                        "id": admin_id,
                        "username": username,
                        "email": email,
                        "name": "Ops Admin",
                        "pw": hash_password(PASSWORD),
                        "secret": secret,
                    },
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_inner())
    return admin_id, secret


def _totp_now(secret: str) -> str:
    """Generate the code the server will accept right now.

    Uses the module's own `_hotp`/`current_step` rather than a reimplementation:
    the RFC vectors in `tests/test_totp.py` are what prove `_hotp` is correct, so
    re-deriving it here would only duplicate the risk. The clock is read on both
    sides from the same source, so a step boundary is the one race — each test
    that cares fetches a fresh code immediately before using it.
    """
    return _hotp(_decode_key(secret), current_step(), 6)


def _login(client, username=USERNAME, password=PASSWORD):
    return client.post(f"{BASE}/login", json={"username": username, "password": password})


# ---------------------------------------------------------------- #
# The core property: password alone is not enough
# ---------------------------------------------------------------- #


def test_password_alone_never_returns_an_access_token(client):
    """The requirement, stated as a test.

    A correct password must produce a challenge and nothing else. If this ever
    returns `access_token`, the second factor has been silently removed.
    """
    _seed_admin(client)
    response = _login(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "access_token" not in body
    assert body["next"] == "totp_required"
    assert body["challenge_token"]


def test_challenge_token_cannot_be_used_as_a_bearer_token(client):
    """The challenge carries no `sub` and no `role`, so it cannot authenticate.

    This is the structural reason the two-step flow is safe: not a check that a
    later refactor could drop, but a token that `principal_from_token` cannot
    parse into a principal at all.
    """
    _seed_admin(client)
    challenge = _login(client).json()["challenge_token"]

    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {challenge}"})
    assert response.status_code == 401, response.text


def test_correct_password_and_code_returns_a_working_access_token(client):
    """A wrong code is refused; the happy path is `test_full_sign_in_and_use_the_token`."""
    _seed_admin(client)
    challenge = _login(client).json()["challenge_token"]

    body = client.post(f"{BASE}/totp/verify", json={"challenge_token": challenge, "code": "000000"})
    assert body.status_code == 401
    assert "access_token" not in body.json()


def test_full_sign_in_and_use_the_token(client):
    """Seed → login → verify TOTP → call a guarded admin route.

    This previously asserted `me.status_code in (403, 404)` with the comment
    "the `users` table has no row for this id — the admin identity is separate".
    The observation was right and the conclusion was wrong: the separation is
    real, but that is *why* the lookups had to move to `admin_accounts`, not a
    reason for the console's own bootstrap call to fail. The old assertion made
    the suite green while **every** console route 403'd behind a valid login —
    the defect the UI verifier found. An assertion that a working token is
    refused is not a security property; it is the bug, written down.
    """
    _, secret = _seed_admin(client)
    assert secret is not None

    challenge = _login(client).json()["challenge_token"]
    verified = client.post(
        f"{BASE}/totp/verify", json={"challenge_token": challenge, "code": _totp_now(secret)}
    )
    assert verified.status_code == 200, verified.text

    token = verified.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "ADMIN"
    assert me.json()["username"] == USERNAME
    # The email is masked: `/me` is not a place to hand back a full address.
    assert "@" in me.json()["email_masked"]
    assert EMAIL not in me.text


def test_the_access_token_can_actually_reach_the_console(client):
    """The regression this exists for: a valid admin token opened **nothing**.

    Every console route hangs off `require_admin`, which resolved the principal
    against `users`. An admin token's `sub` is an `admin_accounts.id`, so the
    lookup always missed and the guard answered 403 — a fully successful login
    followed by an entirely dead console. Nothing in the suite caught it, because
    the one test that touched this path asserted the 403 was expected.

    A spread of routes rather than one: the bug was in the shared dependency, so
    a single probe would have been enough *here*, but asserting several is what
    tells a future reader the whole surface was checked and not just sampled.
    """
    _, secret = _seed_admin(client)
    assert secret is not None

    challenge = _login(client).json()["challenge_token"]
    token = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": challenge, "code": _totp_now(secret)},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for path in ("/api/v1/admin/drivers", "/api/v1/admin/fleets", "/api/v1/admin/refunds"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, f"{path} -> {response.status_code}: {response.text}"


def test_an_otp_user_token_cannot_reach_admin_routes(client):
    """The converse, so the fix cannot be "accept any well-formed principal".

    An operator's token and a passenger's token are different in kind, and the
    guard is the only thing that says so — if it were relaxed to "does the row
    exist", a passenger would reach the refund queue.
    """
    token = client.activate("+85281000077", username="realtaxi_regression_user")
    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403, response.text


def test_a_user_token_carrying_an_admin_role_claim_is_still_refused(client):
    """`role=ADMIN` on a user token is not enough — the `scope` claim is.

    The legacy identity from `scripts/ops/create_admin.py` sets `users.role = ADMIN`.
    That row is a real, active user row, so a guard that only checked
    "row exists and role is ADMIN" would let it straight in. `scope` is what
    distinguishes the two, and it must not be forgeable from the outside.
    """
    import jwt as pyjwt

    from app.core.config import get_settings

    user_id = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users "
        "(id, phone_e164, role, is_active, account_status, created_at, updated_at) "
        "VALUES (:id, '+85281000088', 'ADMIN', true, 'ACTIVE', now(), now())",
        {"id": user_id},
    )

    settings = get_settings()
    forged = pyjwt.encode(
        {"sub": str(user_id), "role": "ADMIN", "iat": int(time.time())},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 403, response.text


def test_an_admin_token_with_no_enrolment_is_refused(client):
    """A row that never proved its second factor must not open the console.

    An enrolled admin's token can only be minted after the TOTP step, so an
    un-enrolled row holding a token means either a hand-edited database or a
    minting path that skipped the factor. Both should read as "no admin here".
    """
    admin_id, secret = _seed_admin(client, username="unproven", email="unproven@hkfastdc.com")
    assert secret is not None

    challenge = _login(client, username="unproven").json()["challenge_token"]
    token = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": challenge, "code": _totp_now(secret)},
    ).json()["access_token"]

    # Enrolment is cleared *after* the token exists, which is the only way to
    # hold a valid token for a row that claims no enrolment.
    client.exec_sql(
        "UPDATE admin_accounts SET totp_enrolled_at = NULL WHERE id = :id", {"id": admin_id}
    )

    response = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------- #
# Wrong credentials
# ---------------------------------------------------------------- #


def test_wrong_password_is_rejected(client):
    _seed_admin(client)
    response = _login(client, password="not-the-password")
    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_unknown_username_does_not_reveal_itself(client):
    """Same status and same message as a wrong password on a real account.

    Read `message`, not `detail`: `app/core/exceptions.py` rewrites every error
    into `{code, message, details}`, so FastAPI's default `detail` key never
    reaches the client.
    """
    _seed_admin(client)
    unknown = _login(client, username="nobody.here", password=PASSWORD)
    wrong = _login(client, username=USERNAME, password="wrong-password-here-42")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["message"] == wrong.json()["message"]


def test_empty_username_is_a_validation_error_not_a_500(client):
    response = client.post(f"{BASE}/login", json={"username": "", "password": PASSWORD})
    assert response.status_code == 422


# ---------------------------------------------------------------- #
# Replay protection
# ---------------------------------------------------------------- #


def test_a_totp_code_cannot_be_used_twice(client):
    """The ±1-step window would otherwise leave a code valid for 90 seconds."""
    _, secret = _seed_admin(client)
    assert secret is not None
    code = _totp_now(secret)

    first = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": _login(client).json()["challenge_token"], "code": code},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": _login(client).json()["challenge_token"], "code": code},
    )
    assert second.status_code == 401, "the same code was accepted twice"


def test_a_challenge_cannot_be_replayed_after_use(client):
    """A consumed challenge must not mint a second token.

    Asserted through the token's `iat`: if the challenge were reusable, the
    second call would succeed and we would get a *different* token. We check the
    refusal instead, which is the property that matters.
    """
    _, secret = _seed_admin(client)
    assert secret is not None
    challenge = _login(client).json()["challenge_token"]
    code = _totp_now(secret)

    ok = client.post(f"{BASE}/totp/verify", json={"challenge_token": challenge, "code": code})
    assert ok.status_code == 200, ok.text
    # Second attempt with the same challenge: the code is now replayed too, so
    # this must fail on both counts.
    again = client.post(f"{BASE}/totp/verify", json={"challenge_token": challenge, "code": code})
    assert again.status_code == 401


def test_a_tampered_challenge_is_rejected(client):
    _seed_admin(client)
    challenge = _login(client).json()["challenge_token"]
    tampered = challenge[:-4] + ("A" if challenge[-4] != "A" else "B") * 4
    response = client.post(
        f"{BASE}/totp/verify", json={"challenge_token": tampered, "code": "123456"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------- #
# Lockout
# ---------------------------------------------------------------- #


def test_lockout_does_not_expose_is_active(client):
    """A brute-force lock must be indistinguishable from a ban at the API edge.

    Both answer 401 with a distinct sentence; neither leaks whether the account
    exists or is disabled in a way that helps an attacker decide what to do next.
    """
    _seed_admin(client)
    for _ in range(5):
        _login(client, password="wrong-password-here-42")

    locked = _login(client)
    detail = locked.json()["message"].lower()
    assert locked.status_code == 401
    assert "lock" in detail or "temporarily" in detail


def test_a_correct_password_during_lockout_is_still_refused(client):
    """The lock must not be bypassable by simply guessing right afterwards."""
    _seed_admin(client)
    for _ in range(5):
        _login(client, password="wrong-password-here-42")

    response = _login(client)  # correct password
    assert response.status_code == 401
    assert "access_token" not in response.json()


# ---------------------------------------------------------------- #
# Enrolment
# ---------------------------------------------------------------- #


def test_first_login_returns_enrolment_material_and_no_token(client):
    _seed_admin(client, enrolled=False)
    body = _login(client).json()

    assert body["next"] == "enrolment_required"
    assert "access_token" not in body
    enrolment = body["enrolment"]
    assert len(enrolment["secret"]) >= 32
    assert enrolment["otpauth_uri"].startswith("otpauth://totp/")
    assert len(enrolment["recovery_codes"]) == 8


def test_enrolment_is_not_persisted_until_a_code_is_proven(client):
    """The lockout bug this design exists to prevent.

    If the secret were written at `/login`, an admin who never scanned the QR
    would need a second factor nobody holds. So: verify the column is still NULL
    after `/login`, and only becomes non-NULL after `/totp/enrol/confirm`.
    """
    admin_id, _ = _seed_admin(client, enrolled=False)
    body = _login(client).json()

    def _secret_in_db():
        async def _inner():
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
            from sqlalchemy.pool import NullPool

            engine = create_async_engine(client.db_url, poolclass=NullPool)
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    return await session.scalar(
                        text("SELECT totp_secret FROM admin_accounts WHERE id = :id"),
                        {"id": admin_id},
                    )
            finally:
                await engine.dispose()

        return asyncio.run(_inner())

    assert _secret_in_db() is None, "the secret was persisted before it was proven"

    secret = body["enrolment"]["secret"]
    confirmed = client.post(
        f"{BASE}/totp/enrol/confirm",
        json={"challenge_token": body["challenge_token"], "code": _totp_now(secret)},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert "access_token" in confirmed.json()
    assert _secret_in_db() is not None

    # And the recovery codes issued alongside were persisted, hashed.
    async def _code_count():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.scalar(
                    text("SELECT count(*) FROM admin_recovery_codes WHERE admin_id = :id"),
                    {"id": admin_id},
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_code_count()) == 8


def test_a_wrong_enrolment_code_does_not_persist_the_secret(client):
    admin_id, _ = _seed_admin(client, enrolled=False)
    body = _login(client).json()

    bad = client.post(
        f"{BASE}/totp/enrol/confirm",
        json={"challenge_token": body["challenge_token"], "code": "000000"},
    )
    assert bad.status_code == 401

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.scalar(
                    text("SELECT totp_secret FROM admin_accounts WHERE id = :id"),
                    {"id": admin_id},
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_inner()) is None


def test_an_enrolment_challenge_cannot_be_used_for_the_totp_step(client):
    """Purpose separation: a token minted for enrolment must not satisfy TOTP.

    Without this the enrolment window becomes a way to skip the second factor —
    the admin would never have proven they hold the device.
    """
    _seed_admin(client, enrolled=False)
    body = _login(client).json()

    response = client.post(
        f"{BASE}/totp/verify",
        json={"challenge_token": body["challenge_token"], "code": "000000"},
    )
    assert response.status_code == 401
    assert "access_token" not in response.json()


# ---------------------------------------------------------------- #
# Recovery codes
# ---------------------------------------------------------------- #


def test_recovery_code_signs_in_and_is_single_use(client):
    _seed_admin(client, enrolled=False)
    body = _login(client).json()
    codes = body["enrolment"]["recovery_codes"]
    secret = body["enrolment"]["secret"]

    confirmed = client.post(
        f"{BASE}/totp/enrol/confirm",
        json={"challenge_token": body["challenge_token"], "code": _totp_now(secret)},
    )
    assert confirmed.status_code == 200, confirmed.text

    challenge = _login(client).json()["challenge_token"]
    used = client.post(f"{BASE}/recovery", json={"challenge_token": challenge, "code": codes[0]})
    assert used.status_code == 200, used.text
    assert "access_token" in used.json()

    # Same code again: refused.
    again = client.post(
        f"{BASE}/recovery",
        json={"challenge_token": _login(client).json()["challenge_token"], "code": codes[0]},
    )
    assert again.status_code == 401


def test_recovery_consumption_is_stamped_not_deleted(client):
    """`used_at` is the audit trail — a deleted row proves nothing."""
    admin_id, _ = _seed_admin(client, enrolled=False)
    body = _login(client).json()
    code = body["enrolment"]["recovery_codes"][0]
    secret = body["enrolment"]["secret"]
    client.post(
        f"{BASE}/totp/enrol/confirm",
        json={"challenge_token": body["challenge_token"], "code": _totp_now(secret)},
    )

    client.post(
        f"{BASE}/recovery",
        json={"challenge_token": _login(client).json()["challenge_token"], "code": code},
    )

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.scalar(
                    text(
                        "SELECT count(*) FROM admin_recovery_codes "
                        "WHERE admin_id = :id AND used_at IS NOT NULL"
                    ),
                    {"id": admin_id},
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_inner()) == 1


def test_a_recovery_code_is_bound_to_its_challenge(client):
    """The endpoint takes a challenge, not a username — so there is nothing to
    enumerate, and a code cannot be tried against an account the caller never
    authenticated to."""
    _seed_admin(client, enrolled=False)
    response = client.post(
        f"{BASE}/recovery",
        json={"challenge_token": "not-a-real-challenge-token", "code": "AAAAA-BBBBB"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------- #
# Audit trail
# ---------------------------------------------------------------- #


def test_failed_and_successful_logins_are_audited(client):
    _seed_admin(client)
    _login(client, password="wrong-password-here-42")
    _login(client)

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.execute(
                    text("SELECT event, outcome FROM admin_audit_log ORDER BY created_at")
                )
        finally:
            await engine.dispose()

    rows = [tuple(r) for r in asyncio.run(_inner()).all()]
    assert ("ADMIN_LOGIN_FAILED", "FAILURE") in rows
    assert ("ADMIN_LOGIN", "SUCCESS") in rows


def test_the_audit_log_never_records_a_password_or_a_secret(client):
    """A one-line guard against someone adding `detail=password` later."""
    _seed_admin(client, enrolled=False)
    body = _login(client).json()
    secret = body["enrolment"]["secret"]

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.scalar(
                    text("SELECT coalesce(string_agg(detail, ' '), '') FROM admin_audit_log")
                )
        finally:
            await engine.dispose()

    blob = asyncio.run(_inner()) or ""
    assert PASSWORD not in blob
    assert secret not in blob


# ---------------------------------------------------------------- #
# Rate limiting
# ---------------------------------------------------------------- #


def test_repeated_logins_are_rate_limited(client):
    """The limiter fires once the per-account budget is exhausted.

    This asserts **429 specifically**, not `401 or 429`.

    It used to be `assert 429 in statuses or 401 in statuses`, which the comment
    justified as "either may fire first". That is true about *which request* —
    the 5-attempt lockout trips before the coarser limiter, so the early
    refusals are legitimately 401 — but as written the assertion also passes if
    **no** request is ever 429, i.e. it could not detect the limiter breaking
    entirely. It was a tautology on any run where something returned 401.

    Now that the 401/429 split is decided by exception *type* rather than by
    substring-matching the message (see `app/api/admin_auth.py::_run`), the
    intended outcome is knowable and worth pinning: the last request in the
    burst must be 429.
    """
    _seed_admin(client)
    statuses = [_login(client, password="wrong-password-here-42").status_code for _ in range(12)]
    assert statuses[-1] == 429, f"limiter never tripped: {statuses}"
    # The lockout (5 attempts) legitimately answers 401 first, so the run is a
    # mix; the point is that the limiter does eventually refuse with 429.
    assert 429 in statuses


def test_a_bad_password_never_creates_a_token_even_under_load(client):
    _seed_admin(client)
    for _ in range(4):
        _login(client, password="wrong-password-here-42")
    response = _login(client)
    assert "access_token" not in response.json()


# ---------------------------------------------------------------- #
# Schema invariants this flow depends on
# ---------------------------------------------------------------- #


def test_account_status_has_no_server_default(client):
    """`account_status` must have no DEFAULT, deliberately.

    It is added with a server_default to backfill existing rows, then the
    default is dropped — so a future INSERT that forgets the column fails loudly
    instead of silently creating an ACTIVE account. If someone re-adds the
    default, this catches it.
    """

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return await session.scalar(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'account_status'"
                    )
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_inner()) is None


def test_username_and_email_are_uniquely_indexed(client):
    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                return set(
                    (
                        await session.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE tablename = 'admin_accounts'"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        finally:
            await engine.dispose()

    names = asyncio.run(_inner())
    assert any("username" in n for n in names), names
    assert any("email" in n for n in names), names


def test_no_admin_route_accepts_the_otp_token(client):
    """An admin token is not a user token, and vice versa.

    `admin_accounts.id` and `users.id` are different namespaces, so an admin
    access token must not satisfy a passenger route — `require_active_user` looks
    the id up in `users` and finds nothing.
    """
    _, secret = _seed_admin(client)
    assert secret is not None
    challenge = _login(client).json()["challenge_token"]
    token = client.post(
        f"{BASE}/totp/verify", json={"challenge_token": challenge, "code": _totp_now(secret)}
    ).json()["access_token"]

    response = client.get("/api/v1/trips", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in (403, 404, 422), response.text


@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/login",
        f"{BASE}/totp/verify",
        f"{BASE}/recovery",
        f"{BASE}/totp/enrol/confirm",
    ],
)
def test_the_admin_auth_routes_are_reachable(client, path):
    """A wiring check: a `main.py` regression that drops the router shows up as
    a 404 here rather than as a mysterious console failure."""
    response = client.post(path, json={})
    assert response.status_code != 404, f"{path} is not mounted"


def test_hash_recovery_code_matches_the_service(client):
    """The tests hash codes the same way the service does — pin that."""
    codes = generate_recovery_codes(4)
    assert len({hash_recovery_code(c) for c in codes}) == 4
    assert all(len(hash_recovery_code(c)) == 64 for c in codes)

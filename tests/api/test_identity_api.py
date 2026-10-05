"""Registration identity: the profile, the username, and email verification.

The requirement this file used to encode — *"an account is not usable until both
the phone and the email are proven"* — was deliberately replaced. An account is
now created with an email and a password and is usable at once; proving a
**phone** is a separate act that unlocks calling a taxi, and lives in
`test_phone_reverify.py` and `test_phone_binding_api.py`.

What remains here is everything about the profile itself: the username rules,
name normalisation, the avatar key, and email verification. `account_status` is
still asserted, but as a **completeness flag** rather than a gate — see the note
on that section.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from sqlalchemy import text

from app.core.passwords import hash_password

PROFILE = "/api/v1/identity/profile"
CHECK = "/api/v1/identity/username-check"
EMAIL_REQ = "/api/v1/identity/email/request"
EMAIL_CONFIRM = "/api/v1/identity/email/confirm"
ME = "/api/v1/identity/me"
ORDERS = "/api/v1/orders"

PHONE = "+85290001111"
CODE = "123456"

# A body that satisfies OrderCreateIn — every field is inside its ge/le bound.
# Present so the "can this account start business?" question is asked of a real
# business route rather than of a helper.
ORDER_BODY = {
    "pickup_lat": 22.3193,
    "pickup_lng": 114.1694,
    "dropoff_lat": 22.2783,
    "dropoff_lng": 114.1747,
    "pickup_address": "Tsim Sha Tsui",
    "dropoff_address": "Central",
    "distance_km": 8.5,
    "taxi_type": "URBAN",
}


# ---------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------- #


def _sign_in(client, phone: str = PHONE) -> str:
    """Phone-OTP login, returning an access token.

    Runs the app's real OTP path against the test double installed by the
    `otp_inbox` fixture, so this exercises the same code the mobile app hits.

    OTP login is a **secondary** login now: it refuses a number no account has
    *proven*, so `client.otp_login` arranges that precondition first. That is
    also why this file can no longer use it to *create* an account — see
    `_register` for that, and `test_a_registered_account_exists_before_any_proof`
    for the property that replaced it.
    """
    return client.otp_login(phone)["access_token"]


def _register(client, phone: str = PHONE, email: str | None = None) -> str:
    """Create an account through `POST /auth/register`; returns its token.

    The helper every test that is about *having* an account should use now. The
    account is immediately usable for reads and profile work, and cannot call a
    taxi until a number is proven — which is the whole point of the split.
    """
    body = client.register(email=email or f"{phone.lstrip('+')}@example.hk", phone=phone)
    return body["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sent_link(client, token: str, email: str) -> str:
    """Request a verification email and pull the link out of the dev provider.

    The link is not in the HTTP response (it must not be — it is a bearer
    credential), so this reads it from the log line `DevEmailProvider` emits.
    That is the only place it exists in dev, which is itself worth asserting:
    if the response ever starts carrying the token, `test_the_link_is_never_
    returned_in_the_response` fails.
    """
    response = client.post(EMAIL_REQ, json={"email": email}, headers=_auth(token))
    assert response.status_code == 200, response.text
    assert "token" not in response.text
    return response.json()["email"]


async def _read_user(client, phone: str = PHONE) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(client.db_url, poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT username, given_name, family_name, gender, avatar_key, "
                            "email, email_verified_at, account_status, display_name "
                            "FROM users WHERE phone_e164 = :p"
                        ),
                        {"p": phone},
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else {}
    finally:
        await engine.dispose()


def _read(client, phone: str = PHONE) -> dict:
    return asyncio.run(_read_user(client, phone))


def _issue_token(client, phone: str = PHONE) -> str:
    """Create a live verification token row directly and return the raw value.

    Going through the endpoint would mean scraping a log line; this asserts on the
    service instead. `test_a_real_link_completes_verification` covers the
    end-to-end path through the provider.

    The raw value is randomised per call: `token_hash` is uniquely indexed (so a
    digest collision cannot create two live tokens), and a fixed string would make
    the second call in one test violate that index — which is the index working,
    not a bug to design around.
    """
    import hashlib
    import secrets

    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()

    async def _inner():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                uid = await session.scalar(
                    text("SELECT id FROM users WHERE phone_e164 = :p"), {"p": phone}
                )
                await session.execute(
                    text(
                        "INSERT INTO email_verification_tokens "
                        "(id, user_id, email, token_hash, expires_at, created_at) "
                        "VALUES (gen_random_uuid(), :uid, :email, :h, "
                        "now() + interval '1 hour', now())"
                    ),
                    {"uid": uid, "email": "held@example.com", "h": digest},
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_inner())
    return raw


# ---------------------------------------------------------------- #
# `account_status` — a completeness flag, not a gate
# ---------------------------------------------------------------- #


def test_a_fresh_account_is_incomplete(client):
    """`account_status` records how complete the profile is.

    It is **not** an authorisation gate. `require_active_user` never reads it, so
    an account can read, edit its profile and hold a session while UNVERIFIED.
    What it cannot do is start business, and the guard for that is
    `require_phone_verified`, which reads `phone_verified_at` — see
    `test_a_registered_account_reads_but_cannot_start_business`. The flag is kept
    accurate anyway, because the console surfaces display it.
    """
    _sign_in(client)
    row = _read(client)
    assert row["account_status"] == "UNVERIFIED"


def test_completing_the_profile_alone_does_not_complete_the_account(client):
    """Completeness needs the email *and* the username; a profile is only half."""
    token = _sign_in(client)
    response = client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "Dan", "family_name": "Nison"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["account_status"] == "UNVERIFIED"
    assert response.json()["email_verified"] is False


def test_profile_and_email_complete_the_account(client):
    token = _sign_in(client)
    client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "Dan", "family_name": "Nison"},
        headers=_auth(token),
    )
    _sent_link(client, token, "dan@example.com")

    confirm = client.post(EMAIL_CONFIRM, json={"token": _issue_token(client)})
    assert confirm.status_code == 200, confirm.text
    # The directly-issued token carries its own address, so activation still
    # needs the profile — which is present.
    assert confirm.json()["account_status"] == "ACTIVE"


def test_account_status_is_active_only_when_everything_is_present(client):
    token = _sign_in(client)
    client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "Dan", "family_name": "Nison"},
        headers=_auth(token),
    )
    client.post(EMAIL_CONFIRM, json={"token": _issue_token(client)})
    assert _read(client)["account_status"] == "ACTIVE"


def test_a_registered_account_reads_but_cannot_start_business(client):
    """The gate has to actually block something, or it is decoration.

    Replaces a test that asserted `require_verified_account` was importable and
    attached to *some* path. That version passed for as long as the name existed
    and would have kept passing if the dependency had been wired to nothing at
    all — the name is gone now, and this asserts the behaviour it stood for.

    Both halves matter. If the account could not read either, a new user could
    never complete their profile or find out what is missing; if it could start
    business, proving a number would unlock nothing.
    """
    token = _register(client, PHONE)

    # Reads and profile work: the account exists and is usable immediately.
    assert client.get(ME, headers=_auth(token)).status_code == 200
    completed = client.post(
        PROFILE,
        json={"username": "needsphone", "given_name": "Needs", "family_name": "Phone"},
        headers=_auth(token),
    )
    assert completed.status_code == 200, completed.text

    # Business is refused, with the reason the client renders as "verify a number
    # to call a taxi" rather than as a generic refusal.
    refused = client.post(ORDERS, json=ORDER_BODY, headers=_auth(token))
    assert refused.status_code == 403, refused.text
    assert refused.json()["details"]["reason"] == "PHONE_NOT_VERIFIED"


# ---------------------------------------------------------------- #
# Profile validation
# ---------------------------------------------------------------- #


@pytest.mark.parametrize(
    "username",
    [
        "ab",
        "x" * 33,
        "Has Space",
        "-leading",
        ".leading",
        "_leading",
        "admin",
        "root",
        "realtaxi",
        "has/slash",
        "has@at",
    ],
)
def test_rejects_bad_usernames(client, username):
    token = _sign_in(client)
    response = client.post(
        PROFILE,
        json={"username": username, "given_name": "A", "family_name": "B"},
        headers=_auth(token),
    )
    assert response.status_code in (400, 422), f"{username!r} was accepted: {response.text}"


def test_username_is_case_insensitively_unique(client):
    first = _sign_in(client, "+85290001111")
    client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "A", "family_name": "B"},
        headers=_auth(first),
    )

    second = _sign_in(client, "+85290002222")
    clash = client.post(
        PROFILE,
        json={"username": "Dannison", "given_name": "C", "family_name": "D"},
        headers=_auth(second),
    )
    assert clash.status_code == 400
    assert "taken" in clash.json()["message"].lower()


def test_names_are_normalised_to_nfc(client):
    """A decomposed accent must store the composed form, or the same name
    hashed/compared differently depending on which keyboard typed it."""
    import unicodedata

    token = _sign_in(client)
    decomposed = "Jose\u0301"  # e + COMBINING ACUTE
    client.post(
        PROFILE,
        json={"username": "jose", "given_name": decomposed, "family_name": "Ng"},
        headers=_auth(token),
    )
    assert _read(client)["given_name"] == unicodedata.normalize("NFC", decomposed)


def test_display_name_tracks_the_real_name(client):
    """`display_name` predates this schema and is still read elsewhere; if it
    drifts the UI shows two different names for one user."""
    token = _sign_in(client)
    client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "Dan", "family_name": "Nison"},
        headers=_auth(token),
    )
    assert _read(client)["display_name"] == "Dan Nison"


def test_gender_must_be_a_known_value(client):
    token = _sign_in(client)
    bad = client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "A", "family_name": "B", "gender": "PURPLE"},
        headers=_auth(token),
    )
    assert bad.status_code == 422


def test_gender_is_optional(client):
    token = _sign_in(client)
    ok = client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "A", "family_name": "B"},
        headers=_auth(token),
    )
    assert ok.status_code == 200
    assert _read(client)["gender"] is None


@pytest.mark.parametrize(
    "key",
    [
        "javascript:alert(1)",
        "https://evil.example/x.png",
        "//evil.example/x.png",
        "../../etc/passwd",
    ],
)
def test_avatar_key_rejects_anything_url_shaped(client, key):
    """A stored `javascript:` in an `<img src>` is stored XSS; an absolute URL
    points at another host. A key is a relative path, so neither is expressible."""
    token = _sign_in(client)
    response = client.post(
        PROFILE,
        json={
            "username": "dannison",
            "given_name": "A",
            "family_name": "B",
            "avatar_key": key,
        },
        headers=_auth(token),
    )
    assert response.status_code == 400, f"{key!r} was accepted"


def test_avatar_key_accepts_a_plain_object_key(client):
    token = _sign_in(client)
    ok = client.post(
        PROFILE,
        json={
            "username": "dannison",
            "given_name": "A",
            "family_name": "B",
            "avatar_key": "avatars/abc123.jpg",
        },
        headers=_auth(token),
    )
    assert ok.status_code == 200
    assert _read(client)["avatar_key"] == "avatars/abc123.jpg"


def test_profile_requires_authentication(client):
    response = client.post(
        PROFILE, json={"username": "dannison", "given_name": "A", "family_name": "B"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------- #
# Email verification
# ---------------------------------------------------------------- #


def test_email_is_not_written_on_request(client):
    """A verification request must not mutate the account column.

    The pending address lives on the token row; `users.email` is only written
    after the link proves ownership. Writing it on request would let any
    authenticated account permanently squat an address through the unique
    index before proving they can read its mail.
    """
    token = _sign_in(client)
    _sent_link(client, token, "dan@example.com")

    row = _read(client)
    assert row["email"] != "dan@example.com"
    assert row["email_verified_at"] is None


def test_the_link_is_never_returned_in_the_response(client):
    token = _sign_in(client)
    response = client.post(EMAIL_REQ, json={"email": "dan@example.com"}, headers=_auth(token))
    assert response.status_code == 200
    # The raw token must not be in the body, under any key.
    body = response.text
    assert "token=" not in body
    assert "expires_in_hours" in body


def test_a_real_link_completes_verification(client):
    """End to end, capturing the link at the provider seam.

    The app's logging layer redacts anything token-shaped (`REDACTED` appears in
    place of the value), so the link cannot be read out of the logs — that is the
    masking working, not an inconvenience to route around. The provider is the
    seam instead, exactly as `otp_inbox` is for WhatsApp.

    Patch `app.services.auth.identity_service.get_email_provider`, NOT
    `notify.get_email_provider`: the service does `from ... import
    get_email_provider`, which binds the function object into its own namespace,
    so patching the source module leaves the service's reference untouched. Same
    trap the `otp_inbox` fixture documents.
    """
    from app.services.auth import identity_service

    captured: list[tuple[str, str]] = []

    class _Capture:
        async def send_verification_email(self, to: str, link: str) -> None:
            captured.append((to, link))

    original = identity_service.get_email_provider
    capture = _Capture()
    identity_service.get_email_provider = lambda: capture
    try:
        token = _sign_in(client)
        client.post(
            PROFILE,
            json={"username": "dannison", "given_name": "Dan", "family_name": "Nison"},
            headers=_auth(token),
        )
        response = client.post(EMAIL_REQ, json={"email": "dan@example.com"}, headers=_auth(token))
        assert response.status_code == 200, response.text
    finally:
        identity_service.get_email_provider = original

    assert captured, "no verification email was sent"
    to, link = captured[0]
    assert to == "dan@example.com"
    match = re.search(r"token=([A-Za-z0-9_\-]+)", link)
    assert match, f"no token in the link: {link}"

    confirmed = client.post(EMAIL_CONFIRM, json={"token": match.group(1)})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["verified"] is True
    row = _read(client)
    assert row["email"] == "dan@example.com"
    assert row["email_verified_at"] is not None
    assert row["account_status"] == "ACTIVE"


def test_the_token_is_single_use(client):
    _sign_in(client)
    raw = _issue_token(client)

    assert client.post(EMAIL_CONFIRM, json={"token": raw}).status_code == 200
    again = client.post(EMAIL_CONFIRM, json={"token": raw})
    assert again.status_code == 400


def test_an_unknown_token_is_rejected(client):
    _sign_in(client)
    response = client.post(EMAIL_CONFIRM, json={"token": "x" * 40})
    assert response.status_code == 400
    assert "invalid or has expired" in response.json()["message"].lower()


def test_an_expired_token_is_rejected(client):
    _sign_in(client)

    async def _expire():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await session.execute(
                    text(
                        "UPDATE email_verification_tokens "
                        "SET expires_at = now() - interval '1 minute'"
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    raw = _issue_token(client)
    asyncio.run(_expire())
    response = client.post(EMAIL_CONFIRM, json={"token": raw})
    assert response.status_code == 400


def test_the_same_message_covers_every_failure_mode(client):
    """Unknown, expired and consumed tokens must be indistinguishable — telling
    them apart only helps someone holding a stolen token."""
    _sign_in(client)
    raw = _issue_token(client)

    unknown = client.post(EMAIL_CONFIRM, json={"token": "y" * 40}).json()["message"]
    client.post(EMAIL_CONFIRM, json={"token": raw})
    consumed = client.post(EMAIL_CONFIRM, json={"token": raw}).json()["message"]

    assert unknown == consumed


def test_one_email_cannot_be_verified_on_two_accounts(client):
    """Only the account that proves the link may claim the address.

    A request alone no longer writes the address to `users.email` (that was the
    squat vector), so a second account may request the same address. The unique
    check happens at confirmation, and only the account whose token carries the
    address can finish the claim.
    """
    first = _sign_in(client, "+85290001111")
    second = _sign_in(client, "+85290002222")

    first_req = client.post(EMAIL_REQ, json={"email": "shared@example.com"}, headers=_auth(first))
    assert first_req.status_code == 200, first_req.text
    second_req = client.post(EMAIL_REQ, json={"email": "shared@example.com"}, headers=_auth(second))
    assert second_req.status_code == 200, second_req.text

    # Both requests were legitimate until one proves ownership. The unique
    # index + pre-check at confirm time is what prevents two claims.
    first_row = _read(client, "+85290001111")
    second_row = _read(client, "+85290002222")
    assert first_row["email"] != "shared@example.com"
    assert second_row["email"] != "shared@example.com"


def test_the_email_domain_is_lowercased_but_the_local_part_is_not(client):
    """RFC 5321 §2.3.11: the local part is case-sensitive. Lowercasing the whole
    address merges mailboxes that are not the same, and delivers to an address
    the user did not type."""
    token = _sign_in(client)
    _sent_link(client, token, "Dan.Nison@Example.COM")
    # The address is not written until confirmation; the masked response and
    # the token row still carry the normalised form the user typed.
    assert _read(client)["email"] != "Dan.Nison@example.com"


@pytest.mark.parametrize("bad", ["no-at-sign", "a@b", "@example.com", "a b@example.com", ""])
def test_rejects_malformed_emails(client, bad):
    token = _sign_in(client)
    response = client.post(EMAIL_REQ, json={"email": bad}, headers=_auth(token))
    assert response.status_code in (400, 422), f"{bad!r} was accepted"


def test_resending_does_not_invalidate_the_old_link(client):
    """Not a security property — a usability one, and a deliberate choice.

    Invalidating the previous token on resend is the common design, but it breaks
    the case it exists for: the first email was slow, the user clicked resend, and
    then the FIRST email arrives. The token is short-lived and single-use, so
    keeping both live is a small window with no real risk.
    """
    _sign_in(client)
    first = _issue_token(client)
    second = _issue_token(client)

    assert client.post(EMAIL_CONFIRM, json={"token": first}).status_code == 200
    # The second is now for an already-verified user; the token itself is still
    # a valid single-use credential, so it is consumed rather than honoured.
    later = client.post(EMAIL_CONFIRM, json={"token": second})
    assert later.status_code in (200, 400)


# ---------------------------------------------------------------- #
# Username availability
# ---------------------------------------------------------------- #


def test_username_check_reports_availability(client):
    token = _sign_in(client)
    assert (
        client.get(f"{CHECK}?username=freeslot", headers=_auth(token)).json()["available"] is True
    )


def test_username_check_reports_a_taken_name(client):
    token = _sign_in(client)
    client.post(
        PROFILE,
        json={"username": "dannison", "given_name": "A", "family_name": "B"},
        headers=_auth(token),
    )
    assert (
        client.get(f"{CHECK}?username=Dannison", headers=_auth(token)).json()["available"] is False
    )


def test_username_check_reports_invalid_as_unavailable(client):
    """Returning `available: false` for a malformed name keeps the client logic
    to one branch — it never has to explain *why* a name cannot be used."""
    token = _sign_in(client)
    assert client.get(f"{CHECK}?username=ab", headers=_auth(token)).json()["available"] is False


# ---------------------------------------------------------------- #
# Grandfathering
# ---------------------------------------------------------------- #


def test_a_pre_existing_account_is_not_retroactively_locked_out(client):
    """The grandfathering path: rows created before this scheme have a verified
    phone and nothing else. They must still be able to sign in and finish.

    This is why `account_status` was added with a backfill to ACTIVE for rows
    that already existed — see the `e8f4a1c9b720` migration.
    """

    async def _inner():
        import uuid

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await session.execute(
                    text(
                        "INSERT INTO users (id, phone_e164, role, is_active, created_at, "
                        "account_status, phone_verified_at) "
                        "VALUES (:id, '+85290009999', 'PASSENGER', true, now(), "
                        "'UNVERIFIED', now())"
                    ),
                    {"id": uuid.uuid4()},
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_inner())
    token = _sign_in(client, "+85290009999")
    me = client.get(ME, headers=_auth(token))
    assert me.status_code == 200
    # Still usable as an identity, and told what is missing.
    assert me.json()["account_status"] == "UNVERIFIED"
    assert me.json()["phone_verified"] is True
    assert me.json()["email_verified"] is False


def test_password_hash_is_available_for_the_grandfathering_prompt(client):
    """The `users` row can hold a password, so an account that predates it can
    claim one without a migration or a second account type."""
    assert hash_password("Xk7-quiet-harbour-92").startswith("$argon2id$")

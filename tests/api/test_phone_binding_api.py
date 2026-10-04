"""Binding and proving a phone number — the call車 unlock.

The change this file guards
---------------------------
Signing in no longer requires a proven number; *starting a booking* does. That
made "prove a phone" a thing a user does to an account they already have, so it
needed its own two endpoints rather than being folded into registration:

    POST /identity/phone/request   send a code, refusing a number somebody else
                                   has already verified
    POST /identity/phone/confirm   prove the code and attach the number

Three properties are worth more than the rest:

1. **The refusal comes before the message.** `request` is the half with a cost
   attached — a billed WhatsApp message — and the half where sending to a number
   the caller can never own is both a nuisance to that number's owner and a free
   way to make the platform pay. The check is therefore *before* `request_otp`,
   and the test asserts no OTP row was written.
2. **The code proves the number being bound.** `confirm` takes the number from
   the request body rather than from a "pending binding" record, so a code issued
   for A cannot attach B. There is no second piece of state to expire or lose.
3. **The partial unique index is the authority, not the service check.**
   `_assert_unclaimed` gives a sentence instead of a 500; the guarantee is
   `uq_users_phone_e164_verified`. The service check is tested here, the index is
   tested by `tests/infra/test_migration_schema_parity.py` and by the "second
   account cannot bind" case below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api import identity as identity_api

PHONE_REQUEST = "/api/v1/identity/phone/request"
PHONE_CONFIRM = "/api/v1/identity/phone/confirm"
PROFILE = "/api/v1/identity/profile"
ME = "/api/v1/identity/me"
ORDERS = "/api/v1/orders"

PHONE = "+85290005001"
OTHER_PHONE = "+85290005002"
RIVAL_PHONE = "+85290005003"

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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _request_code(client, token: str, phone: str):
    return client.post(PHONE_REQUEST, json={"phone_e164": phone}, headers=_auth(token))


def _confirm(client, token: str, phone: str, code: str):
    return client.post(
        PHONE_CONFIRM, json={"phone_e164": phone, "code": code}, headers=_auth(token)
    )


def _bind(client, token: str, phone: str) -> dict:
    """The happy path, through both real endpoints. Returns the confirm body."""
    requested = _request_code(client, token, phone)
    assert requested.status_code == 200, requested.text
    response = _confirm(client, token, phone, client.otp_inbox[phone])
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. The unlock
# --------------------------------------------------------------------------- #


def test_a_registered_account_cannot_order_until_it_proves_a_number(client):
    """The precondition, stated first so every test below has a baseline."""
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403


def test_binding_a_number_unlocks_calling_a_taxi(client):
    """The whole feature in one test: before, refused; after, allowed.

    Split assertions would let a version pass that reports `verified: true` while
    never writing the row, or writes the row while the guard still refuses.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403

    body = _bind(client, token, PHONE)

    assert body["verified"] is True
    assert body["phone_verified"] is True
    assert body["phone_masked"] == "+852****5001"
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 201


def test_binding_schedules_the_first_reverification_window(client):
    """Proving the number starts the monthly clock.

    Without this the account would verify and be reported overdue a moment later,
    because `evaluate` reads a NULL deadline as *due* — correct for a
    grandfathered row, and a trap for a brand-new one.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    body = _bind(client, token, PHONE)

    due = datetime.fromisoformat(body["phone_reverify_due_at"])
    assert due > datetime.now(UTC) + timedelta(days=20)
    assert body["phone_reverify_blocked"] is False
    assert body["phone_reverify_due"] is False


def test_binding_leaves_the_account_incomplete_until_the_email_is_proven(client):
    """`account_status` tracks *proof*, not presence.

    The email is on the row from registration, but it is not verified, so the
    account is still incomplete. `account_status` gates nothing — this is a
    completeness flag the console displays — but it must stay accurate or the
    console lies.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    client.post(
        PROFILE,
        json={"username": "unlocker", "given_name": "Un", "family_name": "Locker"},
        headers=_auth(token),
    )
    body = _bind(client, token, PHONE)
    assert body["account_status"] == "UNVERIFIED"
    assert body["email_verified"] is False


def test_a_bound_number_works_as_a_secondary_login(client):
    """Binding and OTP login are two callers of the same primitive, so a number
    proved here must be usable to sign in later.

    Driven through the login endpoints with **no arrangement at all** — no
    `sign_in` helper, which would have marked the number verified by itself and
    made this pass whether or not the binding worked. The only prior step is
    backdating the consumed code, because `_bind` already used one and the resend
    cooldown would otherwise refuse the fresh request.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    _bind(client, token, PHONE)

    client.exec_sql(
        "UPDATE otp_codes SET created_at = created_at - interval '10 minutes' "
        "WHERE phone_e164 = :p",
        {"p": PHONE},
    )
    requested = client.post("/api/v1/auth/otp/request", json={"phone_e164": PHONE})
    assert requested.status_code == 200, requested.text

    response = client.post(
        "/api/v1/auth/otp/verify",
        json={"phone_e164": PHONE, "code": client.otp_inbox[PHONE]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] is False


# --------------------------------------------------------------------------- #
# 2. Refusals — the number is not available
# --------------------------------------------------------------------------- #


def test_a_number_verified_elsewhere_is_refused_at_the_request(client):
    """Refused **before** the message goes out.

    This is the ordering that matters: `request_otp` costs a billed WhatsApp
    message, and sending one to a number the caller can never own makes the
    platform pay for a message whose only possible outcome is a refusal at the
    next step. Asserted on the effect — no code arrived at the notify seam —
    because a handler that checked afterwards would still return 400.
    """
    owner = client.register(email="owner@example.hk", phone=RIVAL_PHONE)["access_token"]
    _bind(client, owner, RIVAL_PHONE)

    challenger = client.register(email="challenger@example.hk", phone=PHONE)["access_token"]
    # The owner's bind put a code in the inbox for this number, so clear it: the
    # assertion below is about whether the *challenger's* attempt sent one.
    client.otp_inbox.pop(RIVAL_PHONE, None)

    response = _request_code(client, challenger, RIVAL_PHONE)

    assert response.status_code == 400, response.text
    assert "already verified on another account" in response.json()["message"]
    assert RIVAL_PHONE not in client.otp_inbox, "a code was sent for a number that cannot be bound"


def test_a_number_verified_elsewhere_is_refused_at_the_confirm(client):
    """The request-side check races; this is the half that would be reached by a
    caller who already had a code for their *own* number and then tried to bind
    somebody else's."""
    owner = client.register(email="owner@example.hk", phone=RIVAL_PHONE)["access_token"]
    _bind(client, owner, RIVAL_PHONE)

    challenger = client.register(email="challenger@example.hk", phone=PHONE)["access_token"]
    # A genuine code, for the challenger's own number, presented against a number
    # that is already taken.
    _request_code(client, challenger, PHONE)
    response = _confirm(client, challenger, RIVAL_PHONE, client.otp_inbox[PHONE])

    assert response.status_code == 400, response.text
    assert "already verified on another account" in response.json()["message"]


def test_the_same_number_can_be_claimed_by_many_accounts(client):
    """A claim is not a reservation, and this is the denial-of-registration fix.

    The plain UNIQUE that used to sit on `users.phone_e164` meant whoever typed
    your number first owned it, and you could no longer sign up at all — a cheap
    attack, since the number is public. Uniqueness now applies to *verified* rows
    only.
    """
    first = client.register(email="claimer1@example.hk", phone=PHONE)
    second = client.register(email="claimer2@example.hk", phone=PHONE)
    assert first["created"] is True and second["created"] is True

    # Exactly one of them may prove it.
    assert _bind(client, first["access_token"], PHONE)["verified"] is True
    refused = _request_code(client, second["access_token"], PHONE)
    assert refused.status_code == 400, refused.text


def test_a_number_outside_hong_kong_is_refused(client):
    """422 from the pydantic pattern — the shape of the field is wrong, which is
    a different answer from "that number is taken"."""
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    response = client.post(PHONE_REQUEST, json={"phone_e164": "91234567"}, headers=_auth(token))
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# 3. Refusals — the code is wrong, or is for another number
# --------------------------------------------------------------------------- #


def test_a_wrong_code_is_refused_and_binds_nothing(client):
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    _request_code(client, token, PHONE)

    response = _confirm(client, token, PHONE, "000000")
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "BUSINESS_RULE_VIOLATION"
    # The count travels with the refusal — this is why the phone routes re-raise
    # the original exception instead of wrapping it in an `HTTPException`.
    assert response.json()["details"]["attempts_remaining"] == 4

    # And nothing was bound, so the account still cannot order.
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403


def test_a_code_issued_for_another_number_cannot_bind_this_one(client):
    """The reason `confirm` re-derives the number from the body instead of
    trusting a "pending binding" record.

    The code is genuine and unused, and it was sent to a number this account can
    reach — it is simply not the number being bound. A pending-state design would
    accept it, and a code for A would attach B.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    _request_code(client, token, OTHER_PHONE)

    response = _confirm(client, token, PHONE, client.otp_inbox[OTHER_PHONE])
    assert response.status_code == 400, response.text
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403


def test_a_consumed_code_cannot_be_replayed(client):
    """Single-use, and asserted through the endpoint rather than the service —
    `consume_code` flushes, but a route that forgot to commit would roll the
    stamp back and quietly make the code reusable."""
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    code = None
    _request_code(client, token, PHONE)
    code = client.otp_inbox[PHONE]
    assert _confirm(client, token, PHONE, code).status_code == 200

    replay = _confirm(client, token, PHONE, code)
    assert replay.status_code == 400, replay.text
    assert "already used" in replay.json()["message"]


def test_the_attempt_cap_survives_the_refusal(client):
    """Five wrong guesses kill the code even if the sixth is correct.

    This is the test that would fail if `_run_rule` stopped committing: the
    attempt counter is incremented and flushed before the refusal is raised, and
    `get_session` rolls back on exception — so without the commit the counter
    would be discarded and the code would accept unlimited guesses.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    _request_code(client, token, PHONE)
    code = client.otp_inbox[PHONE]

    for _ in range(5):
        assert _confirm(client, token, PHONE, "000000").status_code == 400

    exhausted = _confirm(client, token, PHONE, code)
    assert exhausted.status_code == 400, exhausted.text
    assert "too many attempts" in exhausted.json()["message"]


# --------------------------------------------------------------------------- #
# 4. Authentication and rate limits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", PHONE_REQUEST, {"phone_e164": PHONE}),
        ("post", PHONE_CONFIRM, {"phone_e164": PHONE, "code": "123456"}),
    ],
)
def test_both_halves_require_a_session(client, method, path, body):
    """Neither is public. `request` sends a billed message and `confirm` writes a
    binding, and both act on *the caller's* account — so there is no version of
    this that makes sense without a principal."""
    assert getattr(client, method)(path, json=body).status_code == 401


def test_the_request_is_rate_limited_per_account(client):
    """Per *account*, not only per address.

    A per-IP budget alone would let one account with a pool of addresses send
    unlimited billed messages; this budget is what bounds the spend per account.
    """
    token = client.register(email="unlock@example.hk", phone=PHONE)["access_token"]
    limit = identity_api._PHONE_BIND_ACCOUNT_RATE_LIMIT

    # A distinct number each time, so the per-number OTP cooldown is not what
    # trips — the assertion is about the account budget, not the resend guard.
    for i in range(limit):
        response = _request_code(client, token, f"+8529001{i:04d}")
        assert response.status_code == 200, response.text

    over = _request_code(client, token, "+85290019999")
    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"

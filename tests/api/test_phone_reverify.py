r"""P-4 — monthly phone re-verification.

The user's requirement, verbatim: *"user needs to verify with phone number again
every month."* The decision locked in alongside it was that an overdue re-verify
is a **soft** block — it stops new business, it does not lock the account.

Three properties, in order of importance:

1. **The gate closes only on new business.** `create_order`, `grab_order`,
   `register_driver`, `refund/request` and `submit_licence` refuse; reads and the
   trip lifecycle do not. A driver stranded mid-trip because their number went
   overdue is a support incident, not a security control.
2. **A refusal is always \_due\_ and never a trap.** An overdue account can still
   reach the endpoint that clears the block, and doing so actually clears it.
   This is the loop `mark_verified` closes; without a test the failure mode is
   silent and looks like an outage.
3. **The deadline is derived, not stored as a flag.** `evaluate()` is pure, so its
   boundaries are tested directly rather than through five layers of HTTP.

The 7-day grace window is deliberate and configurable; several tests here pass an
explicit `now` so they assert the boundary rather than the wall clock.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.models import User
from app.services.auth import phone_reverify_service as phone_reverify

CODE = "123456"
PHONE = "+85290003301"
PHONE_DRIVER = "+85290003302"

ORDERS = "/api/v1/orders"
REVERIFY = "/api/v1/identity/phone/reverify"
ME = "/api/v1/identity/me"
DRIVER_REGISTER = "/api/v1/drivers/register"
# The bind flow — proving a number on an account that already exists. This is
# what unlocks calling a taxi, and the only place the first deadline is written.
PHONE_REQUEST = "/api/v1/identity/phone/request"
PHONE_CONFIRM = "/api/v1/identity/phone/confirm"

# A body that satisfies OrderCreateIn — every field is inside its ge/le bound.
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

DRIVER_BODY = {
    "hk_id_last4": "4321",
    "taxi_driver_plate_no": "TD9988",
    "vehicle_reg_mark": "HK1234",
    "taxi_type": "URBAN",
}


# ---------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------- #


async def _run_sql(client, sql: str, params: dict | None = None, *, fetch: bool = False):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(client.db_url, poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            result = await session.execute(text(sql), params or {})
            rows = [dict(r) for r in result.mappings().all()] if fetch else None
            await session.commit()
            return rows
    finally:
        await engine.dispose()


def _exec(client, sql: str, params: dict | None = None) -> None:
    asyncio.run(_run_sql(client, sql, params))


def _one(client, sql: str, params: dict | None = None) -> dict:
    rows = asyncio.run(_run_sql(client, sql, params, fetch=True)) or []
    return rows[0] if rows else {}


def _sign_in(client, phone: str = PHONE) -> str:
    """Phone-OTP login returning an access token.

    OTP login is a **secondary** login now: `OtpService.verify_otp` refuses a
    number no account has *proven*, because otherwise a stolen code would sign
    into whichever account merely *claims* the number. `client.otp_login`
    arranges that precondition and then runs the genuine request/verify pair.

    It also backdates the previous OTP rows, which a second sign-in in one test
    needs: a code is single-use and a resend cooldown blocks a fresh request while
    the old row is recent. Both are production behaviour this file relies on
    elsewhere, and neither is relaxed for the app under test.
    """
    return client.otp_login(phone)["access_token"]


def _bind_phone(client, token: str, phone: str) -> dict:
    """Prove `phone` on the signed-in account: request a code, then confirm it.

    The unlock path, exercised through its two real endpoints rather than by
    writing `phone_verified_at`. `otp_inbox` is the notify seam, so the code is
    read where the app actually sent it.
    """
    requested = client.post(PHONE_REQUEST, json={"phone_e164": phone}, headers=_auth(token))
    assert requested.status_code == 200, requested.text
    code = client.otp_inbox.get(phone, CODE)
    response = client.post(
        PHONE_CONFIRM, json={"phone_e164": phone, "code": code}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _activate(client, phone: str, *, username: str | None = None) -> str:
    """Bring a fresh account to the state the *deadline* is measured against.

    `require_phone_verified` must already be satisfied or the P-4 check is never
    reached — a test about the deadline would then be asserting the wrong
    refusal. `client.otp_login` proves the number; the UPDATE completes the
    profile and pins a fresh deadline, which `_set_due` then moves per test.
    """
    token = _sign_in(client, phone)
    _exec(
        client,
        "UPDATE users SET account_status = 'ACTIVE', phone_verified_at = now(), "
        "phone_reverify_due_at = now() + make_interval(days => :d), "
        "email = COALESCE(email, :e), email_verified_at = COALESCE(email_verified_at, now()), "
        "username = COALESCE(username, :u) WHERE phone_e164 = :p",
        {
            "p": phone,
            "u": username or ("u" + phone[-6:]),
            "e": f"{phone[-8:]}@example.hk",
            "d": get_settings().phone_reverify_interval_days,
        },
    )
    return token


def _set_due(client, phone: str, *, days: float) -> None:
    """Place the re-verify deadline `days` in the future (negative = past).

    Written as an interval rather than a timestamp literal so the test reads as
    "due in the past / the future" and cannot drift with the clock.
    """
    _exec(
        client,
        "UPDATE users SET phone_reverify_due_at = now() + make_interval(secs => :s) "
        "WHERE phone_e164 = :p",
        {"p": phone, "s": days * 86400},
    )


def _due_at(client, phone: str):
    """Read the deadline straight from the row, bypassing the API."""
    row = _one(
        client,
        "SELECT phone_reverify_due_at FROM users WHERE phone_e164 = :p",
        {"p": phone},
    )
    return row["phone_reverify_due_at"]


def _fresh_code(client, phone: str) -> str:
    """Mint a new, unused OTP for `phone` and return it.

    Needed because the sign-in in `_activate` already *consumed* the code it
    used, and a resend cooldown blocks a fresh request while the old row is
    recent. Backdating the row past the cooldown is arrangement, not a bypass:
    single-use codes and the cooldown are production behaviour this file relies
    on elsewhere, and neither is relaxed for the app under test.
    """
    _exec(
        client,
        "UPDATE otp_codes SET created_at = created_at - interval '10 minutes', "
        "consumed_at = NULL WHERE phone_e164 = :p",
        {"p": phone},
    )
    client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    return client.otp_inbox.get(phone, CODE)


# ---------------------------------------------------------------- #
# 1. evaluate() — the pure boundary table
# ---------------------------------------------------------------- #


class _UserStub:
    """A stand-in for the User row. `evaluate` reads exactly one column."""

    def __init__(self, due_at):
        self.phone_reverify_due_at = due_at


def _U(due_at: datetime | None) -> User:
    """`_UserStub` presented as the `User` that `evaluate` declares.

    The cast is the honest statement of what these boundary cases are:
    `evaluate` is documented to read only `phone_reverify_due_at`, and building
    a real `User` would drag in the ORM mapper and a dozen NOT NULL columns that
    none of the cases below touch. A function rather than a class so every call
    site keeps reading `_U(...)`.
    """
    return cast(User, _UserStub(due_at))


_row_seq = 0


def _user_row(**kwargs) -> User:
    """An unpersisted User for the service-level tests.

    Each row still gets its own number, but no longer because the column is
    UNIQUE — that constraint was removed on purpose (see `User.phone_e164`), so
    several accounts may now *claim* the same number. Distinct numbers here keep
    the rows independently identifiable in the assertions below.
    """
    global _row_seq
    _row_seq += 1
    kwargs.setdefault("phone_e164", f"+8529000{_row_seq:04d}")
    return User(**kwargs)


NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def test_null_deadline_is_due_but_not_blocked():
    """A grandfathered row: prompt, do not lock out.

    The distinction matters because every pre-P-4 account starts here. Treating
    NULL as `is_blocked` would take the whole existing user base offline on the
    deploy that introduces this feature — one of those releases where the bug and
    the outage arrive together.
    """
    state = phone_reverify.evaluate(_U(None), now=NOW)
    assert state.is_due is True
    assert state.is_blocked is False
    assert state.grace_ends_at is None
    assert state.days_remaining is None
    assert state.as_dict()["phone_reverify_due_at"] is None


def test_before_the_deadline_nothing_is_due():
    state = phone_reverify.evaluate(_U(NOW + timedelta(days=10)), now=NOW)
    assert state.is_due is False
    assert state.is_blocked is False
    assert state.days_remaining == 10


def test_exactly_on_the_deadline_is_due():
    """The boundary: `now >= due_at`. Not `>`, so the deadline itself counts."""
    state = phone_reverify.evaluate(_U(NOW), now=NOW)
    assert state.is_due is True
    assert state.is_blocked is False


def test_inside_the_grace_window_is_due_but_still_usable():
    grace = get_settings().phone_reverify_grace_days
    state = phone_reverify.evaluate(_U(NOW - timedelta(days=1)), now=NOW)
    assert state.is_due is True
    assert state.is_blocked is False
    assert state.grace_ends_at == NOW - timedelta(days=1) + timedelta(days=grace)
    assert state.days_remaining == grace - 1


def test_past_the_grace_window_is_blocked():
    grace = get_settings().phone_reverify_grace_days
    state = phone_reverify.evaluate(_U(NOW - timedelta(days=grace + 1)), now=NOW)
    assert state.is_due is True
    assert state.is_blocked is True
    # There is no longer a next event to count down to, so the field is None
    # rather than a 0 that would read as "due today" and render a countdown
    # frozen on an account that is already switched off.
    assert state.days_remaining is None


def test_the_grace_window_is_what_makes_it_a_soft_block():
    """Due and blocked are `grace_days` apart, and that gap is the whole design.

    With `grace_days=0` the same row is blocked, which is the assertion that
    proves the window — not the comparison — is what softens the block.
    """
    due = NOW - timedelta(hours=1)
    assert phone_reverify.evaluate(_U(due), now=NOW).is_blocked is False
    assert phone_reverify.evaluate(_U(due), now=NOW, grace_days=0).is_blocked is True


def test_a_naive_deadline_does_not_crash():
    """Assume UTC rather than compare against the host's local clock.

    The column is `DateTime(timezone=True)` so this should not happen — but
    "should not happen" plus a comparison against a naive datetime is a
    `TypeError` at the worst possible moment, and two hosts in different zones
    would then disagree about the same row.
    """
    state = phone_reverify.evaluate(_U(datetime(2026, 6, 1, 12, 0)), now=NOW)
    assert state.is_due is True
    assert state.due_at is not None
    assert state.due_at.tzinfo is not None


def test_as_dict_is_isoformatted_and_machine_readable():
    state = phone_reverify.evaluate(_U(NOW + timedelta(days=3)), now=NOW)
    payload = state.as_dict()
    assert set(payload) == {
        "phone_reverify_due_at",
        "phone_reverify_grace_ends_at",
        "phone_reverify_due",
        "phone_reverify_blocked",
        "phone_reverify_days_remaining",
    }
    # Parseable, not just a string: the client shows a countdown from it.
    datetime.fromisoformat(payload["phone_reverify_due_at"])


def test_next_deadline_uses_the_configured_interval():
    expected = NOW + timedelta(days=get_settings().phone_reverify_interval_days)
    assert phone_reverify.next_deadline(now=NOW) == expected


# ---------------------------------------------------------------- #
# 2. mark_verified — the loop-closing write
# ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mark_verified_stamps_and_reschedules_together(db_session):
    """Both writes are one fact, so both must land.

    Stamping `phone_verified_at` without moving the deadline produces the worst
    bug in this feature: the account verifies and remains blocked. Asserting them
    together is the only way to catch a future refactor that keeps one.
    """
    user = _user_row(phone_verified_at=None)
    db_session.add(user)
    await db_session.flush()
    user.phone_reverify_due_at = NOW - timedelta(days=30)

    await phone_reverify.mark_verified(db_session, user, now=NOW)

    assert user.phone_verified_at == NOW
    assert user.phone_reverify_due_at == phone_reverify.next_deadline(now=NOW)
    # And the consequence: the state flips from blocked to clear.
    assert phone_reverify.evaluate(user, now=NOW).is_blocked is False


@pytest.mark.asyncio
async def test_schedule_for_existing_targets_only_null_rows(db_session):
    """The backfill touches exactly the rows with no deadline, and only those.

    Checked because it runs against the whole user table: a version that
    re-stamped every row would silently reset the clock for accounts that are
    genuinely overdue, which would look like the gate works while quietly not
    enforcing anything.

    The count is asserted against the rows this test created, not as an absolute
    number — the template database carries seed data (a NULL-deadline system row
    among it), so `touched == 3` here would encode the fixture rather than the
    behaviour.
    """
    never = _user_row(phone_verified_at=None)
    proven = _user_row(phone_verified_at=NOW - timedelta(days=60))
    settled = _user_row(phone_verified_at=NOW - timedelta(days=1))
    settled.phone_reverify_due_at = NOW + timedelta(days=20)
    db_session.add_all([never, proven, settled])
    await db_session.flush()

    nulls = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.phone_reverify_due_at.is_(None))
        )
    ).scalar_one()

    touched = await phone_reverify.schedule_for_existing(db_session, now=NOW)

    assert touched == nulls  # every NULL row, and no non-NULL one
    # Re-read through SQL rather than the ORM objects. `expire_all()` then an
    # attribute read triggers a lazy reload, and a lazy reload outside a
    # greenlet context is exactly the `MissingGreenlet` this avoids — the same
    # trap that bit P-3's `selectinload`. Reading columns directly sidesteps the
    # whole question and asserts what is actually in the table.
    stored = dict(
        (
            await db_session.execute(
                select(User.phone_e164, User.phone_reverify_due_at).where(
                    User.phone_e164.in_([never.phone_e164, proven.phone_e164, settled.phone_e164])
                )
            )
        ).all()
    )
    # The three outcomes, checked individually: the live deadline survives, the
    # never-verified row is due now, and the proven one gets a full interval.
    assert stored[settled.phone_e164] == NOW + timedelta(days=20)
    assert stored[never.phone_e164] == NOW
    assert stored[proven.phone_e164] == phone_reverify.next_deadline(now=NOW)


@pytest.mark.asyncio
async def test_backfill_marks_never_verified_rows_due_now(db_session):
    """A row that never proved its phone is due immediately, with grace.

    Setting it to the interval instead would exempt exactly the accounts least
    likely to still control their number — the opposite of the intent.
    """
    never = _user_row(phone_verified_at=None)
    db_session.add(never)
    await db_session.flush()

    await phone_reverify.schedule_for_existing(db_session, now=NOW)

    assert never.phone_reverify_due_at == NOW
    assert phone_reverify.evaluate(never, now=NOW).is_due is True
    assert phone_reverify.evaluate(never, now=NOW).is_blocked is False


# ---------------------------------------------------------------- #
# 3. New business is refused; the account keeps working
# ---------------------------------------------------------------- #


def test_creating_an_order_is_refused_once_overdue(client):
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 1))

    response = client.post(ORDERS, json=ORDER_BODY, headers=_auth(token))

    assert response.status_code == 403, response.text
    detail = response.json()["details"]
    assert detail["reason"] == phone_reverify.REASON_PHONE_REVERIFY_DUE
    # The deadline travels with the refusal so the client can say *when*.
    assert detail["phone_reverify_blocked"] is True
    assert detail["phone_reverify_due_at"] is not None


def test_creating_an_order_works_inside_the_grace_window(client):
    """Due, reminded, and still trading — the point of the grace window."""
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-1)

    response = client.post(ORDERS, json=ORDER_BODY, headers=_auth(token))

    assert response.status_code == 201, response.text


def test_a_current_account_can_create_an_order(client):
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=20)

    response = client.post(ORDERS, json=ORDER_BODY, headers=_auth(token))

    assert response.status_code == 201, response.text


def test_registering_as_a_driver_is_refused_once_overdue(client):
    token = _activate(client, PHONE_DRIVER)
    _set_due(client, PHONE_DRIVER, days=-(get_settings().phone_reverify_grace_days + 5))

    response = client.post(DRIVER_REGISTER, json=DRIVER_BODY, headers=_auth(token))

    assert response.status_code == 403, response.text
    assert response.json()["details"]["reason"] == phone_reverify.REASON_PHONE_REVERIFY_DUE


def test_reads_still_work_while_overdue(client):
    """The soft block must not become a lockout.

    `/identity/me` and the profile are how a user discovers what is wrong. Gating
    reads would leave an overdue account with a 403 and no way to learn why — and
    no way to reach a screen that offers the fix.
    """
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))

    assert client.get(ME, headers=_auth(token)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 200


def test_listing_orders_still_works_while_overdue(client):
    """The lifecycle and history survive; only new business stops."""
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))

    response = client.get(ORDERS, headers=_auth(token))
    assert response.status_code == 200, response.text


def test_an_unproven_phone_gets_the_bind_refusal_not_a_deadline_one(client):
    """The gates are applied in order, and the client renders each differently.

    `require_phone_current` layers on `require_phone_verified`. If the deadline
    check ran first, an account that has never proven a number would be told to
    re-verify one — a confusing and unfixable message, because `/phone/reverify`
    proves a number the account *already* has and would have nothing to prove.

    This replaces the old `ACCOUNT_UNVERIFIED` case: registration no longer
    produces an account that is barred from everything, so the first refusal a
    new user meets is the one about the phone, not about the account.
    """
    body = client.register(email="unproven@example.hk", phone=PHONE)
    response = client.post(ORDERS, json=ORDER_BODY, headers=_auth(body["access_token"]))

    assert response.status_code == 403, response.text
    assert response.json()["details"]["reason"] == "PHONE_NOT_VERIFIED"


# ---------------------------------------------------------------- #
# 4. Re-verifying actually clears the block
# ---------------------------------------------------------------- #


def test_the_reverify_endpoint_is_reachable_while_blocked(client):
    """The remedy is not gated by the condition.

    If `/phone/reverify` depended on `require_phone_current`, an overdue account
    could never clear itself — the one way a soft block turns into a permanent
    lockout, and the reason this dependency is separate.
    """
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))

    # Prove the block is really in force before asserting the endpoint works.
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403

    code = _fresh_code(client, PHONE)
    response = client.post(
        REVERIFY,
        json={"phone_e164": PHONE, "code": code},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert body["phone_reverify_blocked"] is False
    assert body["phone_reverify_days_remaining"] is not None

    # And the deadline really moved on the row, not just in the response.
    assert _due_at(client, PHONE) > datetime.now(UTC) + timedelta(days=20)


def test_new_business_works_again_after_reverifying(client):
    """The full loop: blocked -> verify -> trading again, in one test.

    Split assertions would let a version pass that clears the response field but
    never writes the row; this runs the order that was refused a moment ago.
    """
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403

    code = _fresh_code(client, PHONE)
    assert (
        client.post(
            REVERIFY, json={"phone_e164": PHONE, "code": code}, headers=_auth(token)
        ).status_code
        == 200
    )

    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 201


def test_a_wrong_code_does_not_move_the_deadline(client):
    """A failed re-verify must leave the account exactly as blocked as it was.

    Otherwise a single stray request would silently grant another month, and the
    gate would be decorative.
    """
    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))
    before = _due_at(client, PHONE)

    _fresh_code(client, PHONE)
    response = client.post(
        REVERIFY, json={"phone_e164": PHONE, "code": "000000"}, headers=_auth(token)
    )

    assert response.status_code == 400, response.text
    assert _due_at(client, PHONE) == before


def test_reverify_refuses_a_number_that_is_not_on_the_account(client):
    """Not a phone-change endpoint.

    Accepting a different number here would make this endpoint a silent
    change-of-number primitive — the re-verify is meant to prove a binding the
    account already has.
    """
    token = _activate(client, PHONE)
    other = "+85290003399"
    code = _fresh_code(client, other)

    response = client.post(REVERIFY, json={"phone_e164": other, "code": code}, headers=_auth(token))

    assert response.status_code == 400, response.text
    assert "not the one on your account" in response.text


def test_the_otp_must_belong_to_the_account_not_merely_be_valid(client):
    """A live OTP for another account's number must not move this account's clock.

    This is the cross-account case a `verify_otp`-style lookup-by-phone would
    wave through: the code is genuine, the number is genuine, and only the
    binding between them and the caller is wrong.
    """
    owner = _activate(client, PHONE_DRIVER, username="owner1")
    assert owner is not None  # the other account exists and is signed in elsewhere

    token = _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))
    before = _due_at(client, PHONE)

    # A fresh, entirely valid code — for the *other* number.
    stolen = _fresh_code(client, PHONE_DRIVER)

    response = client.post(
        REVERIFY, json={"phone_e164": PHONE, "code": stolen}, headers=_auth(token)
    )

    # Refused on the number mismatch (it is not PHONE), and the deadline is intact.
    assert response.status_code == 400, response.text
    assert _due_at(client, PHONE) == before


# ---------------------------------------------------------------- #
# 5. Login also counts as proving the number
# ---------------------------------------------------------------- #


def test_logging_in_with_a_fresh_otp_resets_the_clock(client):
    """A daily login is a stronger proof than a re-verify prompt ever gets.

    If only `/phone/reverify` moved the deadline, the users who log in most
    often would drift toward an overdue deadline they never see — the gate would
    fire on exactly the accounts least likely to need it.
    """
    _activate(client, PHONE)
    _set_due(client, PHONE, days=-(get_settings().phone_reverify_grace_days + 30))

    _sign_in(client, PHONE)  # a real OTP login for the same phone

    assert _due_at(client, PHONE) > datetime.now(UTC) + timedelta(days=20)


def test_the_deadline_is_written_by_the_bind_not_by_registration(client):
    """Proving the number is what schedules the first window.

    A freshly *registered* account has proven nothing, so it has no window yet —
    and a NULL deadline is read as *due* by `evaluate`, which is right for a
    grandfathered row but would make a brand-new account prompt for a re-verify
    before it has ever verified once. The fix is that the deadline is written by
    `PhoneBindingService.confirm`, in the same transaction as `phone_verified_at`,
    and registration deliberately leaves the column empty.

    This used to assert the opposite — that a phone-OTP *sign-in* scheduled the
    deadline — because an OTP sign-in was also how an account came into being.
    """
    body = client.register(email="fresh@example.hk", phone=PHONE)
    token = body["access_token"]

    # Registration records a claim, not a proof: no deadline, and no unlock.
    assert _due_at(client, PHONE) is None
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 403

    _bind_phone(client, token, PHONE)

    # Now both halves land together: the proof and the window it opens.
    assert _due_at(client, PHONE) > datetime.now(UTC) + timedelta(days=20)
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 201

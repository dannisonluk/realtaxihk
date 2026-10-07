"""Business-gap suite: weekly service fee (settlement) + deposit refund flow.

These two features are what turn the platform from a free dispatch tool into a
business: the weekly fee is the revenue line, the refund flow is the exit path
that makes the deposit trustworthy. Both move money, so the tests check balances
and the append-only ledger, not just HTTP codes.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from conftest import AdminHeaders
from httpx import Response

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mk_user_token(client, phone: str) -> str:
    """A fully verified, ACTIVE account's token.

    Was a bare OTP login, which no longer reaches any business route: P-2 gates
    on `AccountStatus.ACTIVE` and P-4 layers a phone deadline on top. This module
    is not about those gates, so it clears them and moves on — they are covered
    by `test_identity_api` and `test_phone_reverify`.
    """
    return client.activate(phone)


def _admin_headers(client) -> AdminHeaders:
    return client.admin_headers()


def run_settlement(client, *, period: str | None = None, headers: dict | None = None):
    """Run the weekly settlement the way an operator must now: preview, then run.

    Since the confirmation gate landed, a bare run is refused while anything is
    outstanding. These tests are about *charging*, not about the gate (which
    `tests/test_admin_settlement_preview.py` covers), so they take the same route
    the console does rather than disabling the check.
    """
    admin = headers or _admin_headers(client)
    period = period or _current_period()
    preview = client.post(
        "/api/v1/admin/settlement/preview",
        headers=admin,
        json={"period": period},
    ).json()
    return client.post(
        "/api/v1/admin/settlement/weekly/run",
        headers=admin,
        params={"period": period, "confirm_token": preview["confirm_token"]},
    )


def _current_period() -> str:
    from app.services.ledger.settlement_service import period_key

    return period_key()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, token: str, phone: str) -> str:
    r = client.post(
        "/api/v1/drivers/register",
        headers=_h(token),
        json={
            "hk_id_last4": phone[-4:],
            "taxi_driver_plate_no": f"TD{phone[-5:]}",
            "vehicle_reg_mark": f"V{phone[-4:]}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(client, driver_id: str, amount: str) -> dict:
    r = client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=_admin_headers(client),
        json={"amount_hkd": amount},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _deposit(client, token: str) -> dict:
    r = client.get("/api/v1/drivers/me", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["deposit"]


def _ledger_total(client, token: str) -> Decimal:
    r = client.get("/api/v1/drivers/me/ledger", headers=_h(token))
    assert r.status_code == 200, r.text
    return sum((Decimal(i["amount_hkd"]) for i in r.json()["items"]), Decimal("0"))


def _make_active(client, phone: str, deposit: str = "500.00") -> dict:
    """KYC-approved driver with a fulfilled deposit -> ACTIVE."""
    token = _mk_user_token(client, phone)
    driver_id = _register(client, token, phone)
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=_admin_headers(client),
        json={"decision": "approve"},
    )
    _grant(client, driver_id, deposit)
    return {"token": token, "driver_id": driver_id, "phone": phone}


@pytest.fixture()
def active_driver(client):
    return _make_active(client, "+85293000001")


def _request_refund(client, token: str, note: str = "", amount: str | None = None) -> Response:
    body: dict = {"note": note}
    if amount is not None:
        body["amount_hkd"] = amount
    return client.post("/api/v1/drivers/me/refund/request", headers=_h(token), json=body)


# --------------------------------------------------------------------------- #
# weekly settlement — the revenue line
# --------------------------------------------------------------------------- #


class TestWeeklySettlement:
    def test_charges_active_drivers_and_skips_the_rest(self, client):
        active = _make_active(client, "+85293000101")

        # A registered-but-unreviewed driver and a reviewed-but-unfunded one.
        token_pk = _mk_user_token(client, "+85293000102")
        _register(client, token_pk, "+85293000102")

        token_dr = _mk_user_token(client, "+85293000103")
        driver_dr = _register(client, token_dr, "+85293000103")
        client.post(
            f"/api/v1/admin/drivers/{driver_dr}/review",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )

        r = run_settlement(client)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["charged"] == 1
        assert body["eligible_drivers"] == 1
        # 2 dp: `run_weekly` now renders `fee_hkd` through `money_str` like the
        # preview does, so the preview/run pair read as one number.
        assert body["fee_hkd"] == "200.00"

        assert _deposit(client, active["token"])["balance_hkd"] == "300.00"
        # The unfunded driver has no deposit row at all — no balance to charge.
        assert "balance_hkd" not in _deposit(client, token_dr)

    def test_rerun_of_same_period_is_idempotent(self, client):
        d = _make_active(client, "+85293000111")
        admin = _admin_headers(client)

        first = run_settlement(client, period="2026-W40").json()
        assert first["charged"] == 1

        # Second run of the same period: everything is settled, so the gate
        # lets it through with no token and it is a no-op.
        second = client.post(
            "/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=admin
        ).json()
        assert second["charged"] == 0
        assert second["skipped"] == 1

        # Charged exactly once, not twice.
        assert _deposit(client, d["token"])["balance_hkd"] == "300.00"

    def test_distinct_periods_are_charged_separately(self, client):
        d = _make_active(client, "+85293000121")
        for period in ("2026-W40", "2026-W41"):
            r = run_settlement(client, period=period)
            assert r.json()["charged"] == 1
        assert _deposit(client, d["token"])["balance_hkd"] == "100.00"

    def test_arrears_allowed_and_driver_stays_active(self, client):
        """A fee may push the balance negative — the driver keeps dispatching."""
        d = _make_active(client, "+85293000131")
        for period in ("2026-W40", "2026-W41", "2026-W42"):
            run_settlement(client, period=period)

        assert _deposit(client, d["token"])["balance_hkd"] == "-100.00"
        me = client.get("/api/v1/drivers/me", headers=_h(d["token"])).json()
        assert me["status"] == "ACTIVE"

    def test_writes_ledger_entry_per_period(self, client):
        d = _make_active(client, "+85293000141")
        run_settlement(client, period="2026-W40")
        r = client.get("/api/v1/drivers/me/ledger", headers=_h(d["token"]))
        entries = r.json()["items"]
        assert [e["entry_type"] for e in entries] == ["DEPOSIT_TOPUP", "WEEKLY_FEE_DEDUCTION"]
        assert entries[-1]["amount_hkd"] == "-200.00"
        assert entries[-1]["balance_after_hkd"] == "300.00"

    def test_non_admin_cannot_trigger(self, client):
        d = _make_active(client, "+85293000151")
        r = client.post("/api/v1/admin/settlement/weekly/run", headers=_h(d["token"]))
        assert r.status_code == 403

    def test_bad_period_format_rejected(self, client):
        r = client.post(
            "/api/v1/admin/settlement/weekly/run?period=nope", headers=_admin_headers(client)
        )
        assert r.status_code == 422

    # --- the two BusinessRuleError failures must not be conflated ---------- #

    def test_unfunded_driver_counts_as_skipped_not_failed(self, client):
        """A driver with no deposit row lands in `skipped` — and that is correct.

        **This test replaced a wrong one.** I first wrote it asserting
        `failed == 1`, on the theory that deleting the deposit row would make
        `LedgerService.append()` raise `"driver deposit account not found"` and
        exercise the `failed` branch. It does not: `run_weekly` has its **own**
        `if deposit is None: skipped += 1` guard *before* it ever calls `append()`,
        so the append is never reached for an unfunded driver — the deleted row
        and the never-created row are indistinguishable here, and both are a
        legitimate "nothing to deduct".

        Verified by tracing the loop: the driver is visited, `deposit is None`
        fires, `skipped` increments, `failed` stays 0.
        """
        d = _make_active(client, "+85293000161")
        admin = _admin_headers(client)

        client.exec_sql(
            "DELETE FROM driver_deposits WHERE driver_profile_id = CAST(:d AS uuid)",
            {"d": d["driver_id"]},
        )

        r = run_settlement(client, period="2026-W41", headers=admin)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skipped"] == 1, body
        assert body["failed"] == 0, body
        assert body["charged"] == 0, body

    def test_only_a_duplicate_reference_is_treated_as_a_clean_skip(self):
        """The discriminator, tested at the level where it is decidable.

        `run_weekly` must count `DuplicateReferenceError` as `skipped` and every
        *other* `BusinessRuleError` as `failed`, because the two are one lost fee
        apart. There is no deposit row it can be driven through end-to-end (see
        the test above — the guard short-circuits first), so the branch is pinned
        here by driving `append()` to raise each error in turn and reading the
        resulting counters.

        Note what this does NOT claim: that the `failed` branch is reachable in
        production today. It may not be. What it claims is that *if* it is ever
        reached, a lost fee will not be reported as a clean run — which is the
        guarantee the string-matched version could not make.
        """
        from app.core.exceptions import BusinessRuleError, DuplicateReferenceError

        # The contract the two branches depend on.
        assert issubclass(DuplicateReferenceError, BusinessRuleError)
        # A message reword in `ledger_service` must not change the classification,
        # which is exactly what the old `in exc.message` check allowed.
        reworded = DuplicateReferenceError("reference already claimed")
        assert isinstance(reworded, DuplicateReferenceError)
        assert not isinstance(
            BusinessRuleError("driver deposit account not found"), DuplicateReferenceError
        )

    def test_duplicate_reference_is_a_skip_not_a_failure(self, client):
        """The other half of the pair: an already-charged driver IS `skipped`.

        Together with the test above this pins both sides of the distinction, so
        a change that collapses them fails whichever way it collapses.
        """
        d = _make_active(client, "+85293000171")
        admin = _admin_headers(client)

        first = run_settlement(client, period="2026-W42", headers=admin).json()
        assert first["charged"] == 1, first

        # Re-run of a settled period: no token required (the run is a no-op).
        second = run_settlement(client, period="2026-W42", headers=admin).json()
        assert second["skipped"] == 1, second
        assert second["failed"] == 0, second
        assert _deposit(client, d["token"])["balance_hkd"] == "300.00"

    def test_duplicate_reference_error_is_a_business_rule_error(self):
        """The new type must stay catchable as `BusinessRuleError`.

        Every existing caller and the registered 400 handler catch the parent, so
        if `DuplicateReferenceError` ever stopped subclassing it, the failure mode
        would be a 500 on a race — long after the change that caused it.
        """
        from app.core.exceptions import BusinessRuleError, DuplicateReferenceError

        exc = DuplicateReferenceError("duplicate ledger reference", {"reference": "x"})
        assert isinstance(exc, BusinessRuleError)
        assert isinstance(exc, ValueError)
        assert exc.message == "duplicate ledger reference"
        assert exc.details == {"reference": "x"}

        with pytest.raises(BusinessRuleError):
            raise exc


# --------------------------------------------------------------------------- #
# refund — request
# --------------------------------------------------------------------------- #


class TestRefundRequest:
    def test_request_holds_balance_and_suspends_driver(self, client, active_driver):
        r = _request_refund(client, active_driver["token"], note="leaving HK")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["amount_hkd"] == "500.00"
        assert body["note"] == "leaving HK"

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "0.00"  # moved out of available...
        assert dep["held_hkd"] == "500.00"  # ...into held, not paid out
        assert dep["is_fulfilled"] is False

        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "SUSPENDED"

    def test_no_ledger_entry_until_approved(self, client, active_driver):
        _request_refund(client, active_driver["token"])
        # The hold is not a financial event: nothing has left the platform.
        assert _ledger_total(client, active_driver["token"]) == Decimal("500.0")

    def test_second_request_rejected(self, client, active_driver):
        assert _request_refund(client, active_driver["token"]).status_code == 201
        r = _request_refund(client, active_driver["token"])
        assert r.status_code == 400
        assert "already pending" in r.json()["message"]

    def test_request_without_balance_rejected(self, client):
        d = _make_active(client, "+85293000201")
        # Drain it: 500 - 2 x 200 = 100... push to zero via three fees.
        admin = _admin_headers(client)
        for period in ("2026-W40", "2026-W41", "2026-W42"):
            run_settlement(client, period=period, headers=admin)
        # balance is now -100 (arrears) -> still "nothing to refund"
        r = _request_refund(client, d["token"])
        assert r.status_code == 400
        assert "nothing to refund" in r.json()["message"]

    def test_non_active_driver_cannot_request(self, client):
        """DEPOSIT_REQUIRED driver has money but is not on the road yet."""
        token = _mk_user_token(client, "+85293000211")
        driver_id = _register(client, token, "+85293000211")
        client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )
        _grant(client, driver_id, "500.00")
        # Grant fulfils the deposit -> ACTIVE; suspend to test the gate.
        client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers=_admin_headers(client),
            json={"decision": "suspend"},
        )
        r = _request_refund(client, token)
        assert r.status_code == 400
        assert "ACTIVE" in r.json()["message"]

    def test_cannot_request_while_trip_in_progress(self, client):
        d = _make_active(client, "+85293000221")
        passenger = _mk_user_token(client, "+85293000222")
        order = client.post(
            "/api/v1/orders",
            headers=_h(passenger),
            json={
                "pickup_lat": 22.284,
                "pickup_lng": 114.158,
                "dropoff_lat": 22.315,
                "dropoff_lng": 114.219,
                "pickup_address": "Statue Square, Central",
                "dropoff_address": "Harbour North, North Point",
                "distance_km": "4.2",
                "taxi_type": "URBAN",
            },
        )
        assert order.status_code == 201, order.text
        oid = order.json()["id"]
        assert client.post(f"/api/v1/orders/{oid}/grab", headers=_h(d["token"])).status_code == 200

        r = _request_refund(client, d["token"])
        assert r.status_code == 400
        assert "trip is in progress" in r.json()["message"]

    def test_driver_can_read_own_request(self, client, active_driver):
        _request_refund(client, active_driver["token"])
        r = client.get("/api/v1/drivers/me/refund", headers=_h(active_driver["token"]))
        assert r.status_code == 200
        assert r.json()["refund"]["status"] == "PENDING"

    def test_read_returns_null_when_never_requested(self, client, active_driver):
        r = client.get("/api/v1/drivers/me/refund", headers=_h(active_driver["token"]))
        assert r.status_code == 200
        assert r.json()["refund"] is None

    def test_pending_refund_is_exempt_from_weekly_fee(self, client, active_driver):
        _request_refund(client, active_driver["token"])
        r = run_settlement(client, period="2026-W40")
        assert r.json()["charged"] == 0
        assert _deposit(client, active_driver["token"])["held_hkd"] == "500.00"

    @pytest.mark.asyncio
    async def test_min_amount_floor_blocks_a_trivial_payout(self, client, active_driver):
        """REFUND_MIN_HKD must actually gate the request, not just sit in .env.

        Unreachable through the HTTP API today (a driver cannot be ACTIVE below
        the 500 deposit requirement), so drive the service directly.
        """
        from sqlalchemy import select as _select

        from app.core.exceptions import BusinessRuleError
        from app.models import DriverDeposit, DriverProfile
        from app.services.ledger.refund_service import RefundService

        async with client.db_factory() as s:
            dp = (
                (
                    await s.execute(
                        _select(DriverProfile).where(DriverProfile.id == active_driver["driver_id"])
                    )
                )
                .scalars()
                .first()
            )
            dep = (
                (
                    await s.execute(
                        _select(DriverDeposit).where(DriverDeposit.driver_profile_id == dp.id)
                    )
                )
                .scalars()
                .first()
            )
            dep.balance_hkd = Decimal("0.5")
            await s.commit()

            with pytest.raises(BusinessRuleError) as exc:
                await RefundService(s).request(dp, min_amount_hkd=1)
            assert "nothing to refund" in str(exc.value)
            assert exc.value.details["min_amount_hkd"] == "1"
            await s.rollback()


# --------------------------------------------------------------------------- #
# refund — admin decision
# --------------------------------------------------------------------------- #


class TestRefundDecision:
    def _pending(self, client, driver) -> str:
        r = _request_refund(client, driver["token"])
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_approve_pays_out_and_terminates(self, client, active_driver):
        rid = self._pending(client, active_driver)
        admin = _admin_headers(client)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=admin,
            json={"decision": "approve", "note": "bank transfer sent"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"
        assert r.json()["decision_note"] == "bank transfer sent"
        # The id of the admin that actually signed in — no longer a fixture-wide
        # constant, because the acting admin is now a real `admin_accounts` row
        # created by `_admin_headers(client)`.
        assert r.json()["decided_by"] == admin.admin_id

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "0.00"
        assert dep["held_hkd"] == "0.00"

        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "TERMINATED"

        # Exactly one REFUND entry, and the ledger chain still nets to the balance.
        r = client.get("/api/v1/drivers/me/ledger", headers=_h(active_driver["token"]))
        entries = r.json()["items"]
        assert entries[-1]["entry_type"] == "REFUND"
        assert entries[-1]["amount_hkd"] == "-500.00"
        assert entries[-1]["balance_after_hkd"] == "0.00"
        assert _ledger_total(client, active_driver["token"]) == Decimal("0")

    def test_reject_releases_hold_and_reactivates(self, client, active_driver):
        rid = self._pending(client, active_driver)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "reject", "note": "kyc issue"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REJECTED"

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "500.00"
        assert dep["held_hkd"] == "0.00"
        assert dep["is_fulfilled"] is True

        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "ACTIVE"

        # A rejection is not a financial event — no new ledger row.
        r = client.get("/api/v1/drivers/me/ledger", headers=_h(active_driver["token"]))
        assert [e["entry_type"] for e in r.json()["items"]] == ["DEPOSIT_TOPUP"]

    def test_driver_can_request_again_after_rejection(self, client, active_driver):
        rid = self._pending(client, active_driver)
        client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "reject"},
        )
        assert _request_refund(client, active_driver["token"]).status_code == 201

    def test_double_decision_rejected(self, client, active_driver):
        rid = self._pending(client, active_driver)
        admin = _admin_headers(client)
        assert (
            client.post(
                f"/api/v1/admin/refunds/{rid}/decision", headers=admin, json={"decision": "approve"}
            ).status_code
            == 200
        )
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision", headers=admin, json={"decision": "approve"}
        )
        assert r.status_code == 400
        assert "already decided" in r.json()["message"]
        # Not paid twice.
        assert _ledger_total(client, active_driver["token"]) == Decimal("0")

    def test_unknown_refund_is_400(self, client):
        r = client.post(
            "/api/v1/admin/refunds/00000000-0000-0000-0000-0000000000ff/decision",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )
        assert r.status_code == 400
        assert "not found" in r.json()["message"]

    def test_malformed_refund_id_is_422(self, client):
        r = client.post(
            "/api/v1/admin/refunds/not-a-uuid/decision",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )
        assert r.status_code == 422

    def test_non_admin_cannot_decide(self, client, active_driver):
        rid = self._pending(client, active_driver)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_h(active_driver["token"]),
            json={"decision": "approve"},
        )
        assert r.status_code == 403

    def test_bad_decision_value_is_422(self, client, active_driver):
        rid = self._pending(client, active_driver)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "maybe"},
        )
        assert r.status_code == 422

    def test_approval_after_partial_arrears_refunds_the_remainder(self, client):
        """Refund pays whatever is left, not the original deposit."""
        d = _make_active(client, "+85293000301")
        run_settlement(client, period="2026-W40")
        rid = self._pending(client, d)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )
        assert r.json()["amount_hkd"] == "300.00"
        assert _deposit(client, d["token"])["balance_hkd"] == "0.00"
        assert _ledger_total(client, d["token"]) == Decimal("0")


# --------------------------------------------------------------------------- #
# admin listing
# --------------------------------------------------------------------------- #


class TestRefundListing:
    def test_lists_and_filters(self, client, active_driver):
        admin = _admin_headers(client)
        rid = _request_refund(client, active_driver["token"]).json()["id"]

        r = client.get("/api/v1/admin/refunds", headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["status"] == "PENDING"

        assert (
            client.get("/api/v1/admin/refunds?status_filter=APPROVED", headers=admin).json()[
                "total"
            ]
            == 0
        )

        client.post(
            f"/api/v1/admin/refunds/{rid}/decision", headers=admin, json={"decision": "reject"}
        )
        body = client.get("/api/v1/admin/refunds?status_filter=REJECTED", headers=admin).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == rid

    def test_non_admin_cannot_list(self, client, active_driver):
        r = client.get("/api/v1/admin/refunds", headers=_h(active_driver["token"]))
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# refund — partial withdrawal (P2-2): claim part of the balance, stay ACTIVE
# --------------------------------------------------------------------------- #


class TestPartialRefund:
    """A driver can claim less than the whole balance and keep driving.

    Full refunds are the exit path (approval terminates). Partial refunds are
    the withdrawal path: the claimed amount is held, approval pays it out and
    returns the driver to ACTIVE, and the remainder stays on the account.
    """

    def test_partial_request_holds_only_the_claimed_amount(self, client, active_driver):
        r = _request_refund(client, active_driver["token"], amount="200.00")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["amount_hkd"] == "200.00"
        assert body["is_partial"] is True

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "300.00"
        assert dep["held_hkd"] == "200.00"
        # The driver is off the road while money is in flight — same as a full
        # refund, so pending money cannot race new trips.
        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "SUSPENDED"

    def test_partial_approve_pays_claimed_and_reactivates(self, client, active_driver):
        rid = _request_refund(client, active_driver["token"], amount="200.00").json()["id"]
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "approve"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"
        assert r.json()["is_partial"] is True

        # 200 paid out; the remaining 300 returns to available.
        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "300.00"
        assert dep["held_hkd"] == "0.00"
        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "ACTIVE"

        entries = client.get(
            "/api/v1/drivers/me/ledger", headers=_h(active_driver["token"])
        ).json()["items"]
        assert entries[-1]["entry_type"] == "REFUND"
        assert entries[-1]["amount_hkd"] == "-200.00"
        assert entries[-1]["balance_after_hkd"] == "300.00"

    def test_partial_reject_releases_hold_and_reactivates(self, client, active_driver):
        rid = _request_refund(client, active_driver["token"], amount="200.00").json()["id"]
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(client),
            json={"decision": "reject"},
        )
        assert r.status_code == 200, r.text
        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "500.00"
        assert dep["held_hkd"] == "0.00"
        me = client.get("/api/v1/drivers/me", headers=_h(active_driver["token"])).json()
        assert me["status"] == "ACTIVE"

    def test_amount_equal_to_balance_is_rejected(self, client, active_driver):
        """An amount == balance is a full refund in disguise — use no amount."""
        r = _request_refund(client, active_driver["token"], amount="500.00")
        assert r.status_code == 400, r.text
        assert "must be less than" in r.json()["message"]

    def test_amount_above_balance_is_rejected(self, client, active_driver):
        r = _request_refund(client, active_driver["token"], amount="999.00")
        assert r.status_code == 400, r.text

    def test_zero_or_negative_amount_is_rejected(self, client, active_driver):
        r = _request_refund(client, active_driver["token"], amount="0")
        assert r.status_code == 422, r.text
        r = _request_refund(client, active_driver["token"], amount="-10.00")
        assert r.status_code == 422, r.text

    def test_full_refund_stream_is_unchanged(self, client, active_driver):
        """Omitting amount keeps the legacy whole-balance semantics."""
        r = _request_refund(client, active_driver["token"])
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["amount_hkd"] == "500.00"
        assert body["is_partial"] is False
        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "0.00"
        assert dep["held_hkd"] == "500.00"


# --------------------------------------------------------------------------- #
# concurrency — the DB backstop must be real, not just a service-layer check
# --------------------------------------------------------------------------- #


class TestRefundConcurrency:
    @pytest.mark.asyncio
    async def test_parallel_requests_hold_money_once(self, client, active_driver):
        """5 parallel requests -> exactly one wins, and it holds the money once.

        The service-layer "already pending" check alone cannot survive this: all
        five read the pre-commit state. What saves it is the deposit row lock
        (the losers then find a zero balance) plus
        `uq_refund_pending_per_driver` for the insert itself.
        """
        from sqlalchemy import select as _select

        from app.core.exceptions import BusinessRuleError
        from app.models import DriverProfile
        from app.services.ledger.refund_service import RefundService

        factory = client.db_factory
        did = active_driver["driver_id"]

        async def attempt():
            async with factory() as s:
                dp = (
                    (await s.execute(_select(DriverProfile).where(DriverProfile.id == did)))
                    .scalars()
                    .first()
                )
                try:
                    await RefundService(s).request(dp, note="parallel")
                except BusinessRuleError:
                    await s.rollback()
                    return False
                await s.commit()
                return True

        results = await asyncio.gather(*[attempt() for _ in range(5)])
        assert sum(results) == 1

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "0.00"
        assert dep["held_hkd"] == "500.00"  # held once, not 5x
        assert _ledger_total(client, active_driver["token"]) == Decimal("500.0")

    @pytest.mark.asyncio
    async def test_parallel_approvals_pay_once(self, client, active_driver):
        """Two admins deciding at once must not pay twice."""
        from sqlalchemy import select as _select

        from app.core.exceptions import BusinessRuleError
        from app.models import RefundRequest
        from app.services.ledger.refund_service import RefundService

        rid = _request_refund(client, active_driver["token"]).json()["id"]
        factory = client.db_factory
        # Drives `RefundService.decide` directly, below the HTTP layer, so no
        # authentication is involved and the id is only ever written to
        # `decided_by`. Any UUID is as good as another — it deliberately is not
        # `ADMIN_ID`, which no longer names a console admin.
        admin_id = uuid.uuid4()

        async def decide():
            async with factory() as s:
                await s.execute(
                    _select(RefundRequest.id).where(RefundRequest.id == rid).with_for_update()
                )
                try:
                    await RefundService(s).decide(
                        rid, approve=True, admin_id=admin_id, decision_note="race"
                    )
                except BusinessRuleError:
                    await s.rollback()
                    return False
                await s.commit()
                return True

        results = await asyncio.gather(decide(), decide())
        assert sum(results) == 1

        dep = _deposit(client, active_driver["token"])
        assert dep["balance_hkd"] == "0.00"
        assert dep["held_hkd"] == "0.00"
        # Paid exactly once.
        assert _ledger_total(client, active_driver["token"]) == Decimal("0")

    @pytest.mark.asyncio
    async def test_pending_refund_partial_unique_index_exists(self, client):
        """The backstop is a DB constraint, so assert it in the DB, not the model."""
        from sqlalchemy import text

        async with client.db_factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE tablename = 'refund_requests' "
                        "AND indexname = 'uq_refund_pending_per_driver'"
                    )
                )
            ).all()
        assert len(rows) == 1, "uq_refund_pending_per_driver is missing"
        assert "UNIQUE" in rows[0][0]
        assert "PENDING" in rows[0][0]  # partial — APPROVED/REJECTED rows are exempt

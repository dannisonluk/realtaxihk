"""Business-gap suite: weekly service fee (settlement) + deposit refund flow.

These two features are what turn the platform from a free dispatch tool into a
business: the weekly fee is the revenue line, the refund flow is the exit path
that makes the deposit trustworthy. Both move money, so the tests check balances
and the append-only ledger, not just HTTP codes.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from conftest import ADMIN_ID

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mk_user_token(client, phone: str) -> str:
    r = client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    assert r.status_code == 200, r.text
    # SEC-02: the code is never in the response; read it at the notify seam.
    r = client.post(
        "/api/v1/auth/otp/verify",
        json={"phone_e164": phone, "code": client.otp_inbox[phone]},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _admin_headers() -> dict:
    from app.core.security import create_access_token

    return {"Authorization": "Bearer " + create_access_token({"sub": ADMIN_ID, "role": "ADMIN"})}


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
        headers=_admin_headers(),
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
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    _grant(client, driver_id, deposit)
    return {"token": token, "driver_id": driver_id, "phone": phone}


@pytest.fixture()
def active_driver(client):
    return _make_active(client, "+85293000001")


def _request_refund(client, token: str, note: str = "") -> dict:
    return client.post("/api/v1/drivers/me/refund/request", headers=_h(token), json={"note": note})


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
            headers=_admin_headers(),
            json={"decision": "approve"},
        )

        r = client.post("/api/v1/admin/settlement/weekly/run", headers=_admin_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["charged"] == 1
        assert body["eligible_drivers"] == 1
        assert body["fee_hkd"] == "200"

        assert _deposit(client, active["token"])["balance_hkd"] == "300.00"
        # The unfunded driver has no deposit row at all — no balance to charge.
        assert "balance_hkd" not in _deposit(client, token_dr)

    def test_rerun_of_same_period_is_idempotent(self, client):
        d = _make_active(client, "+85293000111")
        admin = _admin_headers()

        first = client.post(
            "/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=admin
        ).json()
        assert first["charged"] == 1

        second = client.post(
            "/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=admin
        ).json()
        assert second["charged"] == 0
        assert second["skipped"] == 1

        # Charged exactly once, not twice.
        assert _deposit(client, d["token"])["balance_hkd"] == "300.00"

    def test_distinct_periods_are_charged_separately(self, client):
        d = _make_active(client, "+85293000121")
        admin = _admin_headers()
        for period in ("2026-W40", "2026-W41"):
            r = client.post(f"/api/v1/admin/settlement/weekly/run?period={period}", headers=admin)
            assert r.json()["charged"] == 1
        assert _deposit(client, d["token"])["balance_hkd"] == "100.00"

    def test_arrears_allowed_and_driver_stays_active(self, client):
        """A fee may push the balance negative — the driver keeps dispatching."""
        d = _make_active(client, "+85293000131")
        admin = _admin_headers()
        for period in ("2026-W40", "2026-W41", "2026-W42"):
            client.post(f"/api/v1/admin/settlement/weekly/run?period={period}", headers=admin)

        assert _deposit(client, d["token"])["balance_hkd"] == "-100.00"
        me = client.get("/api/v1/drivers/me", headers=_h(d["token"])).json()
        assert me["status"] == "ACTIVE"

    def test_writes_ledger_entry_per_period(self, client):
        d = _make_active(client, "+85293000141")
        client.post("/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=_admin_headers())
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
        r = client.post("/api/v1/admin/settlement/weekly/run?period=nope", headers=_admin_headers())
        assert r.status_code == 422


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
        admin = _admin_headers()
        for period in ("2026-W40", "2026-W41"):
            client.post(f"/api/v1/admin/settlement/weekly/run?period={period}", headers=admin)
        client.post("/api/v1/admin/settlement/weekly/run?period=2026-W42", headers=admin)
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
            headers=_admin_headers(),
            json={"decision": "approve"},
        )
        _grant(client, driver_id, "500.00")
        # Grant fulfils the deposit -> ACTIVE; suspend to test the gate.
        client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers=_admin_headers(),
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
        r = client.post(
            "/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=_admin_headers()
        )
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
        from app.services.refund_service import RefundService

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
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(),
            json={"decision": "approve", "note": "bank transfer sent"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"
        assert r.json()["decision_note"] == "bank transfer sent"
        assert r.json()["decided_by"] == ADMIN_ID

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
            headers=_admin_headers(),
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
            headers=_admin_headers(),
            json={"decision": "reject"},
        )
        assert _request_refund(client, active_driver["token"]).status_code == 201

    def test_double_decision_rejected(self, client, active_driver):
        rid = self._pending(client, active_driver)
        admin = _admin_headers()
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
            headers=_admin_headers(),
            json={"decision": "approve"},
        )
        assert r.status_code == 400
        assert "not found" in r.json()["message"]

    def test_malformed_refund_id_is_422(self, client):
        r = client.post(
            "/api/v1/admin/refunds/not-a-uuid/decision",
            headers=_admin_headers(),
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
            headers=_admin_headers(),
            json={"decision": "maybe"},
        )
        assert r.status_code == 422

    def test_approval_after_partial_arrears_refunds_the_remainder(self, client):
        """Refund pays whatever is left, not the original deposit."""
        d = _make_active(client, "+85293000301")
        client.post("/api/v1/admin/settlement/weekly/run?period=2026-W40", headers=_admin_headers())
        rid = self._pending(client, d)
        r = client.post(
            f"/api/v1/admin/refunds/{rid}/decision",
            headers=_admin_headers(),
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
        admin = _admin_headers()
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
        from app.services.refund_service import RefundService

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
        from app.services.refund_service import RefundService

        rid = _request_refund(client, active_driver["token"]).json()["id"]
        factory = client.db_factory
        admin_id = ADMIN_ID

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

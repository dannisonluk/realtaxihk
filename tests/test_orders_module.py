"""TDD — Module B: order create, atomic grab (Redis SETNX), lifecycle, penalties.

The atomic-grab test hammers one BROADCASTING order with 6 concurrent
service-level grabs: exactly one must win, the rest must lose cleanly.
"""

import asyncio

import pytest


def _mk_user_token(client, phone: str) -> str:
    """A fully verified, ACTIVE account's token.

    Was a bare OTP login, which no longer reaches any business route: P-2 gates
    on `AccountStatus.ACTIVE` and P-4 layers a phone deadline on top. This module
    is not about those gates, so it clears them and moves on — they are covered
    by `test_identity_api` and `test_phone_reverify`.
    """
    return client.activate(phone)


def _admin_headers(client) -> dict:
    return client.admin_headers()


def _mk_active_driver(client, phone: str) -> dict:
    token = _mk_user_token(client, phone)
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": phone[-4:],
            "taxi_driver_plate_no": f"TD{phone[-5:]}",
            "vehicle_reg_mark": f"V{phone[-4:]}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    driver_id = r.json()["id"]
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=_admin_headers(client),
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=_admin_headers(client),
        json={"amount_hkd": "500.00"},
    )
    # fetch user_id via /auth/me
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"]}


_ORDER = {
    "pickup_lat": 22.284,
    "pickup_lng": 114.158,  # Central
    "dropoff_lat": 22.315,
    "dropoff_lng": 114.219,  # North Point
    "pickup_address": "Statue Square, Central",
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}


def _create_order(client, passenger_token: str) -> dict:
    r = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {passenger_token}"},
        json=_ORDER,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def passenger_token(client):
    return _mk_user_token(client, "+85291500001")


class TestOrderLifecycle:
    def test_create_order_broadcasting(self, client, passenger_token):
        body = _create_order(client, passenger_token)
        assert body["status"] == "BROADCASTING"
        assert body["taxi_type"] == "URBAN"
        # fare snapshot embedded (Cap. 374D disclaimers survive in snapshot)
        assert body["fare"]["disclaimer_en"]
        assert body["fare"]["tariff_version"]
        assert float(body["fare"]["total_fare"]) > 0

    def test_validation_rejects_bad_payload(self, client, passenger_token):
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json={"pickup_lat": 22.284},  # missing required fields
        )
        assert r.status_code == 422

    def test_unauthenticated_rejected(self, client):
        assert client.post("/api/v1/orders", json=_ORDER).status_code == 401

    def test_passenger_cancel_own_order(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        r = client.post(
            f"/api/v1/orders/{oid}/cancel",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json={"reason": "no longer needed"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "CANCELLED"

    def test_grab_requires_active_driver(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        token = _mk_user_token(client, "+85291500002")  # plain passenger
        r = client.post(f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_full_lifecycle_to_completed(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d = _mk_active_driver(client, "+85291500003")
        h = {"Authorization": f"Bearer {d['token']}"}
        assert client.post(f"/api/v1/orders/{oid}/grab", headers=h).json()["status"] == "ACCEPTED"
        r = client.post(f"/api/v1/orders/{oid}/arrive", headers=h)
        assert r.json()["status"] == "DRIVER_ARRIVED"
        assert client.post(f"/api/v1/orders/{oid}/start", headers=h).json()["status"] == "IN_TRIP"
        r = client.post(f"/api/v1/orders/{oid}/complete", headers=h)
        assert r.json()["status"] == "COMPLETED"
        assert r.json()["completed_at"]

    def test_illegal_transition_rejected(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d = _mk_active_driver(client, "+85291500004")
        h = {"Authorization": f"Bearer {d['token']}"}
        client.post(f"/api/v1/orders/{oid}/grab", headers=h)
        r = client.post(f"/api/v1/orders/{oid}/start", headers=h)  # skip arrive
        assert r.status_code == 400

    def test_second_grab_after_accepted_conflicts(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d1 = _mk_active_driver(client, "+85291500005")
        d2 = _mk_active_driver(client, "+85291500006")
        assert (
            client.post(
                f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {d1['token']}"}
            ).status_code
            == 200
        )
        r = client.post(
            f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {d2['token']}"}
        )
        assert r.status_code == 409

    def test_driver_cancel_after_accept_penalized(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d = _mk_active_driver(client, "+85291500007")
        h = {"Authorization": f"Bearer {d['token']}"}
        client.post(f"/api/v1/orders/{oid}/grab", headers=h)
        r = client.post(f"/api/v1/orders/{oid}/cancel", headers=h, json={"reason": "cant make it"})
        assert r.status_code == 200
        ledger = client.get("/api/v1/drivers/me/ledger", headers=h).json()["items"]
        penalties = [i for i in ledger if i["entry_type"] == "PENALTY_DEDUCTION"]
        assert penalties and penalties[0]["amount_hkd"] == "-50.00"
        assert penalties[0]["order_id"] == oid


class TestAtomicGrab:
    def test_exactly_one_service_grab_wins(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        drivers = [_mk_active_driver(client, f"+852{915 * 10**5 + 15100 + i}") for i in range(6)]
        user_ids = [d["user_id"] for d in drivers]

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services.order.grab_service import GrabService

        async def hammer():
            engine = create_async_engine(client.db_url, poolclass=NullPool)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            import redis.asyncio as aioredis

            rds = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            try:
                return await asyncio.gather(
                    *[
                        GrabService(rds, factory).grab(order_id=oid, driver_user_id=uid)
                        for uid in user_ids
                    ],
                    return_exceptions=True,
                )
            finally:
                await engine.dispose()
                await rds.aclose()

        outcomes = asyncio.run(hammer())
        trues = [o for o in outcomes if o is True]
        assert len(trues) == 1, outcomes
        for o in outcomes:
            assert o is True or o is False, f"unexpected outcome: {o!r}"


class TestConcurrentCancel:
    def test_two_cancels_charge_one_penalty(self, client, passenger_token):
        """The no-show penalty is decided from a read, so that read must be locked.

        `order_cancel` reads the order, asserts the transition, appends the
        penalty, and only then writes the new status. Two concurrent cancels from
        the assigned driver both saw `ACCEPTED`, both passed the guard, and both
        appended HK$50 — two ledger rows for one cancellation.
        `_get_order(for_update=True)` makes the read and the guard atomic: the
        second request blocks on the row lock, re-reads `CANCELLED`, and is
        refused before it reaches the ledger.

        Driven through the real handler rather than a re-implementation of its
        logic, so the test cannot drift away from the route it protects.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.api.orders import CancelIn, order_cancel
        from app.core.deps import principal_from_token

        oid = _create_order(client, passenger_token)["id"]
        drv = _mk_active_driver(client, "+85291500031")
        h = {"Authorization": f"Bearer {drv['token']}"}
        assert client.post(f"/api/v1/orders/{oid}/grab", headers=h).status_code == 200

        principal = principal_from_token(drv["token"])

        class _Request:
            """`order_cancel` only reaches for `request` on the BROADCASTING
            branch, to drop the Redis geo entry. This order is ACCEPTED, so it is
            never touched — and if that ever changes, the AttributeError is a
            loud failure rather than a silent pass."""

        async def hammer():
            engine = create_async_engine(client.db_url, poolclass=NullPool)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

            async def cancel_once():
                async with factory() as s:
                    try:
                        await order_cancel(
                            oid, CancelIn(reason="double tap"), _Request(), principal, s
                        )
                    except Exception as exc:  # the refusal is the point
                        await s.rollback()
                        return type(exc).__name__
                    await s.commit()
                    return "cancelled"

            try:
                return await asyncio.gather(cancel_once(), cancel_once(), return_exceptions=True)
            finally:
                await engine.dispose()

        outcomes = sorted(str(o) for o in asyncio.run(hammer()))
        assert outcomes == ["BusinessRuleError", "cancelled"], outcomes

        ledger = client.get("/api/v1/drivers/me/ledger", headers=h).json()["items"]
        penalties = [i for i in ledger if i["entry_type"] == "PENALTY_DEDUCTION"]
        assert len(penalties) == 1, (
            f"one cancellation produced {len(penalties)} penalties: {penalties}. "
            "The penalty is appended from a read of `orders.status`, so that read "
            "has to be taken with FOR UPDATE."
        )
        assert penalties[0]["amount_hkd"] == "-50.00"


class TestStateMachineInvariants:
    """Structural properties of `ORDER_TRANSITIONS`, not of any one endpoint.

    These are cheap to assert and expensive to break silently. A bad edit to the
    dict — the kind that looks like tidying — makes the machine self-inconsistent
    without failing any behavioural test, because the affected transition simply
    never gets exercised.

    Every invariant here corresponds to a claim in the `state_machine` module
    docstring. If one is being deliberately relaxed, that docstring has to change
    in the same commit.
    """

    def test_terminal_states_have_no_outgoing_edges(self):
        """COMPLETED and CANCELLED are dead ends.

        A non-empty row would let a finished trip be reopened, and every
        earnings figure derived from `orders` (weekly settlement, analytics)
        would then be computed over a moving target.
        """
        from app.models import OrderStatus
        from app.services.order.state_machine import ORDER_TRANSITIONS

        for terminal in (OrderStatus.COMPLETED, OrderStatus.CANCELLED):
            assert ORDER_TRANSITIONS[terminal] == set(), (
                f"{terminal.value} is terminal but has outgoing edges: "
                f"{ORDER_TRANSITIONS[terminal]}"
            )

    def test_cancelled_is_unreachable_once_the_trip_is_under_way(self):
        """A started trip ends by completion, never by cancellation.

        P4 will add INTERRUPTED for the mid-trip case; CANCELLED must stay
        unreachable from IN_TRIP even then, otherwise the two overlap and a
        cancelled trip could still carry in-trip ledger entries.
        """
        from app.models import OrderStatus
        from app.services.order.state_machine import ORDER_TRANSITIONS

        assert OrderStatus.CANCELLED not in ORDER_TRANSITIONS[OrderStatus.IN_TRIP]

    def test_every_status_is_a_key(self):
        """No status may be missing from the table.

        `.get(current, set())` in the assert helper means a missing key reads as
        "nothing is legal from here" — a silent, total lockout of that state
        rather than an error. Requiring a key makes forgetting one a test
        failure instead of a production dead end.
        """
        from app.models import OrderStatus
        from app.services.order.state_machine import ORDER_TRANSITIONS

        missing = set(OrderStatus) - set(ORDER_TRANSITIONS)
        assert not missing, f"statuses with no transition row: {missing}"

    def test_every_target_is_a_known_status(self):
        """No edge points at a status outside the enum."""
        from app.models import OrderStatus
        from app.services.order.state_machine import ORDER_TRANSITIONS

        known = set(OrderStatus)
        for source, targets in ORDER_TRANSITIONS.items():
            unknown = targets - known
            assert not unknown, f"{source.value} -> unknown targets {unknown}"

    def test_completed_is_only_reachable_from_in_trip(self):
        """Exactly one path to COMPLETED.

        A second inbound edge would mean a trip could be marked complete without
        ever having been started, which is the shape a fare-fraud attempt takes:
        complete an order that was never driven.
        """
        from app.models import OrderStatus
        from app.services.order.state_machine import ORDER_TRANSITIONS

        sources = {
            src for src, targets in ORDER_TRANSITIONS.items() if OrderStatus.COMPLETED in targets
        }
        assert sources == {OrderStatus.IN_TRIP}, sources

    def test_driver_terminated_is_terminal(self):
        from app.models import DriverStatus
        from app.services.order.state_machine import DRIVER_TRANSITIONS

        assert DRIVER_TRANSITIONS[DriverStatus.TERMINATED] == set()

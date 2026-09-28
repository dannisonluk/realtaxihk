"""TDD — Module B: order create, atomic grab (Redis SETNX), lifecycle, penalties.

The atomic-grab test hammers one BROADCASTING order with 6 concurrent
service-level grabs: exactly one must win, the rest must lose cleanly.
"""
import asyncio

import pytest


def _mk_user_token(client, phone: str) -> str:
    r = client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    r = client.post(
        "/api/v1/auth/otp/verify", json={"phone_e164": phone, "code": r.json()["dev_code"]}
    )
    return r.json()["access_token"]


def _admin_headers() -> dict:
    from app.core.security import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {"sub": "00000000-0000-0000-0000-0000000000aa", "role": "ADMIN"}
        )
    }


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
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=_admin_headers(),
        json={"amount_hkd": "500.00"},
    )
    # fetch user_id via /auth/me
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"]}


_ORDER = {
    "pickup_lat": 22.284, "pickup_lng": 114.158,   # Central
    "dropoff_lat": 22.315, "dropoff_lng": 114.219, # North Point
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
        r = client.post(
            f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_full_lifecycle_to_completed(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d = _mk_active_driver(client, "+85291500003")
        h = {"Authorization": f"Bearer {d['token']}"}
        assert client.post(f"/api/v1/orders/{oid}/grab", headers=h).json()["status"] == "ACCEPTED"
        assert client.post(f"/api/v1/orders/{oid}/arrive", headers=h).json()["status"] == "DRIVER_ARRIVED"
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
        assert client.post(
            f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {d1['token']}"}
        ).status_code == 200
        r = client.post(
            f"/api/v1/orders/{oid}/grab", headers={"Authorization": f"Bearer {d2['token']}"}
        )
        assert r.status_code == 409

    def test_driver_cancel_after_accept_penalized(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        d = _mk_active_driver(client, "+85291500007")
        h = {"Authorization": f"Bearer {d['token']}"}
        client.post(f"/api/v1/orders/{oid}/grab", headers=h)
        r = client.post(
            f"/api/v1/orders/{oid}/cancel", headers=h, json={"reason": "cant make it"}
        )
        assert r.status_code == 200
        ledger = client.get(
            "/api/v1/drivers/me/ledger", headers=h
        ).json()["items"]
        penalties = [i for i in ledger if i["entry_type"] == "PENALTY_DEDUCTION"]
        assert penalties and penalties[0]["amount_hkd"] == "-50.0"
        assert penalties[0]["order_id"] == oid


class TestAtomicGrab:
    def test_exactly_one_service_grab_wins(self, client, passenger_token):
        oid = _create_order(client, passenger_token)["id"]
        drivers = [_mk_active_driver(client, f"+852{915*10**5+15100+i}") for i in range(6)]
        user_ids = [d["user_id"] for d in drivers]

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services.grab_service import GrabService

        async def hammer():
            engine = create_async_engine(client.db_url, poolclass=NullPool)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            import redis.asyncio as aioredis

            rds = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            try:
                return await asyncio.gather(
                    *[
                        GrabService(rds, factory).grab(
                            order_id=oid, driver_user_id=uid
                        )
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

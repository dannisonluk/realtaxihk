"""TDD — Module D: WebSocket GPS streaming + live tracking.

WS channel /ws/trip/{order_id}:
- auth via ?token= (browser WS has no header channel);
- role authority: assigned driver pushes; passenger (order owner) + driver read;
- driver ticks land at DB + Redis GEO + Pub/Sub;
- passenger receives driver ticks in real time;
- lifecycle events (grab/cancel) published on the same channel;
- REST snapshot GET /api/v1/trips/{order_id}/location for reconnection.
"""

from __future__ import annotations

import json
import time

import pytest  # shared at top: no bottom-of-file import
from starlette.testclient import TestClient  # noqa: F401


def _mk_user_token(client, phone: str) -> str:
    r = client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    r = client.post(
        "/api/v1/auth/otp/verify", json={"phone_e164": phone, "code": r.json()["dev_code"]}
    )
    return r.json()["access_token"]


def _admin_token() -> str:
    from app.core.security import create_access_token

    return create_access_token({"sub": "00000000-0000-0000-0000-0000000000aa", "role": "ADMIN"})


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
    assert r.status_code in (200, 201), r.text
    driver_id = r.json()["id"]
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"amount_hkd": "500.00"},
    )
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"]}


_ORDER = {
    "pickup_lat": 22.284,
    "pickup_lng": 114.158,
    "dropoff_lat": 22.315,
    "dropoff_lng": 114.219,
    "pickup_address": "Statue Square, Central",
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}


def _mk_broadcasting_order(client, pax_token: str) -> str:
    r = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {pax_token}"},
        json=_ORDER,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ws_url(client, path: str, token: str) -> str:
    return f"ws://testserver{path}?token={token}"


class TestWsAuth:
    def test_ws_requires_token(self, client):
        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000011"))
        ws = client.websocket_connect(f"ws://testserver/ws/trip/{oid}")
        ws.__enter__()
        try:
            msg = ws.receive()
        finally:
            ws.__exit__(None, None, None)
        assert msg["type"] == "websocket.close" and msg["code"] == 4401

    def test_ws_rejects_bad_token(self, client):
        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000012"))
        ws = client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", "junk.token.here"))
        ws.__enter__()
        try:
            msg = ws.receive()
        finally:
            ws.__exit__(None, None, None)
        assert msg["type"] == "websocket.close" and msg["code"] == 4401


class TestWsStreaming:
    def test_driver_push_passenger_receives(self, client):
        pax = _mk_user_token(client, "+85260000021")
        drv = _mk_active_driver(client, "+85260000022")
        oid = _mk_broadcasting_order(client, pax)
        grab = client.post(
            f"/api/v1/orders/{oid}/grab",
            headers={"Authorization": f"Bearer {drv['token']}"},
        )
        assert grab.status_code == 200, grab.text

        received: list[dict] = []

        def passenger_reader():
            with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", pax)) as ws:
                for _ in range(3):
                    received.append(json.loads(ws.receive_text()))

        import threading

        t = threading.Thread(target=passenger_reader, daemon=True)
        t.start()

        # wait for the passenger socket to be registered before pushing
        time.sleep(1.5)

        with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", drv["token"])) as ws:
            for i in range(3):
                ws.send_text(json.dumps({"lat": 22.280 + i * 0.001, "lng": 114.15 + i * 0.001}))
                ws.receive_text()  # barrier: server processed the tick (ack/echo)

        t.join(timeout=10)
        assert not t.is_alive(), "passenger thread did not receive 3 ticks in time"
        assert [m["type"] for m in received] == ["location"] * 3
        assert received[0]["lat"] == pytest.approx(22.280)
        assert received[2]["lat"] == pytest.approx(22.282)

    def test_non_active_driver_cannot_push(self, client):
        pax = _mk_user_token(client, "+85260000031")
        drv = _mk_active_driver(client, "+85260000032")
        oid = _mk_broadcasting_order(client, pax)
        grab = client.post(
            f"/api/v1/orders/{oid}/grab",
            headers={"Authorization": f"Bearer {drv['token']}"},
        )
        assert grab.status_code == 200

        # suspend the driver directly at the DB layer (per-test clone DB)
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.deps import principal_from_token
        from app.models import DriverProfile, DriverStatus

        async def _suspend():
            engine = create_async_engine(client.db_url, poolclass=NullPool)
            Session = async_sessionmaker(engine, expire_on_commit=False)
            async with Session() as s:
                uid = principal_from_token(drv["token"]).id
                prof = (
                    (await s.execute(select(DriverProfile).where(DriverProfile.user_id == uid)))
                    .scalars()
                    .first()
                )
                prof.status = DriverStatus.SUSPENDED
                await s.commit()
            await engine.dispose()

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_suspend())

        with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", drv["token"])) as ws:
            ws.send_text(json.dumps({"lat": 22.28, "lng": 114.15}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "error"
            assert reply["code"] == "DRIVER_NOT_ACTIVE"

    def test_snapshot_endpoint_after_ticks(self, client):
        pax = _mk_user_token(client, "+85260000041")
        drv = _mk_active_driver(client, "+85260000042")
        oid = _mk_broadcasting_order(client, pax)
        grab = client.post(
            f"/api/v1/orders/{oid}/grab",
            headers={"Authorization": f"Bearer {drv['token']}"},
        )
        assert grab.status_code == 200

        with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", drv["token"])) as ws:
            ws.send_text(json.dumps({"lat": 22.285, "lng": 114.161}))
            ws.receive_text()  # barrier: tick processed before snapshot read

        r = client.get(
            f"/api/v1/trips/{oid}/location",
            headers={"Authorization": f"Bearer {pax}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lat"] == pytest.approx(22.285)
        assert body["driver_id"] == drv["user_id"]

    def test_snapshot_requires_participant(self, client):
        pax = _mk_user_token(client, "+85260000051")
        other = _mk_user_token(client, "+85260000052")
        drv = _mk_active_driver(client, "+85260000053")
        oid = _mk_broadcasting_order(client, pax)
        assert (
            client.post(
                f"/api/v1/orders/{oid}/grab",
                headers={"Authorization": f"Bearer {drv['token']}"},
            ).status_code
            == 200
        )

        r = client.get(
            f"/api/v1/trips/{oid}/location",
            headers={"Authorization": f"Bearer {other}"},
        )
        assert r.status_code == 403

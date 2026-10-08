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
    """A fully verified, ACTIVE account's token.

    Was a bare OTP login, which no longer reaches any business route: P-2 gates
    on `AccountStatus.ACTIVE` and P-4 layers a phone deadline on top. This module
    is not about those gates, so it clears them and moves on — they are covered
    by `test_identity_api` and `test_phone_reverify`.
    """
    return client.activate(phone)


def _admin_token(client) -> str:
    # `admin_headers()` returns a bearer header; the call sites below build
    # their own `f"Bearer {...}"`, so hand back the bare token.
    return client.admin_headers()["Authorization"].split(" ", 1)[1]


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
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
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
        """SEC-14: authorization completes BEFORE the 101 upgrade, so an
        unauthenticated client is rejected outright rather than being handed a
        live socket and a task on the server."""
        from starlette.websockets import WebSocketDisconnect

        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000011"))
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(f"ws://testserver/ws/trip/{oid}"),
        ):
            pass
        assert exc.value.code == 4401

    def test_ws_rejects_bad_token(self, client):
        from starlette.websockets import WebSocketDisconnect

        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000012"))
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", "junk.token.here")),
        ):
            pass
        assert exc.value.code == 4401

    def test_ws_rejects_cross_origin_before_accept(self, client):
        """SEC-33: a browser page from an untrusted origin cannot open the WS."""
        from starlette.websockets import WebSocketDisconnect

        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000013"))
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(
                _ws_url(client, f"/ws/trip/{oid}", "valid.token.not.checked"),
                headers={"Origin": "https://evil.example"},
            ),
        ):
            pass
        assert exc.value.code == 4403

    def test_ws_allows_configured_origin(self, client):
        """A browser on one of the configured CORS origins may use the socket."""
        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000014"))
        with client.websocket_connect(
            _ws_url(client, f"/ws/trip/{oid}", _mk_user_token(client, "+85260000014")),
            headers={"Origin": "http://localhost:3000"},
        ):
            pass

    def test_ws_accepts_bearer_header_token(self, client):
        """Native mobile sends the token in an Authorization header (dart:io
        WebSocket has a header channel; the browser cannot). The server must
        accept it without a ?token= query parameter."""
        pax = _mk_user_token(client, "+85260000015")
        oid = _mk_broadcasting_order(client, pax)
        with client.websocket_connect(
            f"ws://testserver/ws/trip/{oid}",
            headers={"Authorization": f"Bearer {pax}"},
        ):
            pass

    def test_ws_rejects_bad_bearer_header_token(self, client):
        """A garbage bearer header is refused like a garbage query token."""
        from starlette.websockets import WebSocketDisconnect

        oid = _mk_broadcasting_order(client, _mk_user_token(client, "+85260000016"))
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(
                f"ws://testserver/ws/trip/{oid}",
                headers={"Authorization": "Bearer junk.token.here"},
            ),
        ):
            pass
        assert exc.value.code == 4401


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
                assert prof is not None, "the registered driver must have a profile row"
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
        # SEC-27: the snapshot identifies the driver by profile id, never by the
        # account UUID that signs their tokens.
        assert body["driver_profile_id"] == drv["driver_id"]
        assert body["driver_profile_id"] != drv["user_id"]

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

    def test_driver_tick_outside_hk_is_refused(self, client):
        """A driver outside Hong Kong may not publish positions.

        The refusal is an explicit error rather than silence, and it is
        **fatal**: the coordinate is refused however many times it is sent, so
        the client must not treat it like the retryable `RATE_LIMITED` hiccup.
        Guangzhou (23.1291, 113.2644) sits outside `HK_BBOX`, so this exercises
        the cheap reject rather than the polygon ray cast -- the polygon is
        covered separately by `tests/domain/test_hk_bounds.py`.

        The mobile client pins this exact payload in
        `mobile/test/fixtures/ws_outside_hk_error.json`; if the shape here
        changes, that fixture and its decoder must change with it.
        """
        pax = _mk_user_token(client, "+85260000071")
        drv = _mk_active_driver(client, "+85260000072")
        oid = _mk_broadcasting_order(client, pax)
        assert (
            client.post(
                f"/api/v1/orders/{oid}/grab",
                headers={"Authorization": f"Bearer {drv['token']}"},
            ).status_code
            == 200
        )

        with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", drv["token"])) as ws:
            for _ in range(2):
                ws.send_text(json.dumps({"lat": 23.1291, "lng": 113.2644}))
                reply = json.loads(ws.receive_text())
                assert reply == {"type": "error", "code": "OUTSIDE_HK"}

        # Nothing was published: the snapshot is still a 200 with a null fix,
        # because a participant asking early is not an error — the driver
        # simply has no position on record. A 404 here would mean the order
        # vanished, which is not what a refused tick should cause.
        snapshot = client.get(
            f"/api/v1/trips/{oid}/location",
            headers={"Authorization": f"Bearer {pax}"},
        )
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["lat"] is None
        assert snapshot.json()["lng"] is None


class TestWsConnectionLifetime:
    """The handshake must not hold a pooled DB connection for the socket's life.

    This is the one defect the rest of the suite structurally cannot see:
    `tests/conftest.py` overrides `get_session` **and** `get_session_factory`
    with a `NullPool` engine, and NullPool has no ceiling to exhaust. Production
    runs `db_pool_size + db_max_overflow` = 30 connections per process against a
    `ws_max_connections_total` of 2000 — so the 31st concurrent live trip
    exhausts the pool, and from then on every *other* request in that process
    stalls on `pool_timeout` before failing. The live-trip socket is the core
    feature, so this is not a corner.
    """

    def test_the_route_declares_no_request_scoped_session(self):
        """The structural half.

        `Depends(get_session)` on a WebSocket is torn down when the handler
        returns — at socket close, not at the end of the handshake. Any session
        taken that way is held for the whole conversation, so this route must not
        declare one; it opens a session per unit of work from `factory()`.

        Introspected through `app.api.ws.router.routes`, not `app.routes`:
        `include_router` is not flattened in this FastAPI version, so the app's
        own route list does not contain the WebSocket route at all.
        """
        from fastapi.routing import APIWebSocketRoute

        from app.api.ws import router
        from app.core.db import get_session

        # `isinstance`, not `getattr`: only `APIWebSocketRoute` carries
        # `.dependant`, which is what the assertion below reads.
        (route,) = [
            r
            for r in router.routes
            if isinstance(r, APIWebSocketRoute) and r.path == "/ws/trip/{order_id}"
        ]
        declared = {dep.call for dep in route.dependant.dependencies}

        assert get_session not in declared, (
            "the WebSocket route takes Depends(get_session). For a WS that "
            "dependency lives until the socket closes, so the pooled connection "
            "behind it is held for hours. Use Depends(get_session_factory) and "
            "open a session per unit of work instead."
        )
        assert any(getattr(call, "__name__", "") == "get_session_factory" for call in declared), (
            "the route must still take its factory from the DI graph, or tests cannot override it"
        )

    def test_the_handshake_releases_its_connection(self, client):
        """The behavioural half, measured on the Postgres server.

        Counted in `pg_stat_activity` rather than through SQLAlchemy's pool
        because the suite's factory is NullPool, which has no `checkedout()` to
        read and no ceiling to hit. The measurement is a delta against a baseline
        taken just before the socket opens, so the probe's own connection — and
        anything still winding down from the setup requests — cancels.
        """
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        def _backends() -> int:
            """Backends this test database currently has, including this probe's
            own — which is present in every measurement and therefore cancels."""

            async def _count():
                engine = create_async_engine(client.db_url, poolclass=NullPool)
                try:
                    async with engine.connect() as conn:
                        return (
                            await conn.execute(
                                text(
                                    "select count(*) from pg_stat_activity "
                                    "where datname = current_database()"
                                )
                            )
                        ).scalar_one()
                finally:
                    await engine.dispose()

            return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_count())

        pax = _mk_user_token(client, "+85260000061")
        oid = _mk_broadcasting_order(client, pax)

        # Baseline taken with the setup requests settled.
        #
        # Comparing against a *before* reading, not an *after* one, is load
        # bearing. With the defect the leaked connection is still sitting
        # `idle in transaction` long after the socket closes — the dependency
        # teardown is not prompt in this harness — so an after-reading is
        # inflated by the very thing under test and the two sides cancel out.
        # Measured, not assumed: that version of this test passed against the
        # defect.
        time.sleep(0.3)
        baseline = _backends()

        with client.websocket_connect(_ws_url(client, f"/ws/trip/{oid}", pax)) as ws:
            # Send a tick and take the refusal. This proves the reader loop is
            # running, so the measurement is taken on a socket that really
            # completed its handshake rather than one that never opened.
            ws.send_text(json.dumps({"lat": 22.285, "lng": 114.155}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "error"
            assert reply["code"] == "READ_ONLY"

            inside = _backends()
            assert inside <= baseline, (
                f"the open WebSocket holds {inside - baseline} more database "
                f"connection(s) than before it connected ({inside} vs {baseline}). "
                "The handshake is keeping its session alive for the life of the "
                "socket; with a 30-connection pool and a 2000-socket cap, the "
                "31st live trip would starve every other request in the process."
            )

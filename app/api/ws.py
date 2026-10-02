"""Module D — WebSocket live-trip channel: /ws/trip/{order_id}.

Auth model (browser WS has no header channel):
  ws://host/ws/trip/{order_id}?token=<access JWT>

Authority rules:
- passenger (order owner): read-only subscriber;
- assigned driver: pusher + subscriber; pushes re-check driver status ACTIVE
  per tick (a suspended driver cannot keep streaming);
- everyone else: denied at handshake (4403).

Close codes: 4401 unauthenticated, 4403 forbidden, 4404 unknown order,
4408 connection cap reached.

Protocol:
- driver sends {"lat": float, "lng": float} → receives {"type":"ack"} per
  processed tick, or {"type":"error","code": ...};
- subscriber pump delivers {"type":"location"} ticks and lifecycle events;
- the driver's own pump does NOT echo location ticks (direct ack instead),
  so the driver socket sees exactly one reply per sent tick.

Security (SEC-14/16/18/30):
- authorization completes BEFORE `accept()`, so an unauthenticated client never
  gets a 101 upgrade or a server-side socket/task;
- one shared `TripHub` (single Redis client) serves every socket, plus per-user
  and global connection caps, so one account cannot exhaust Redis's `maxclients`;
- inbound ticks are throttled per connection (DB write + Pub/Sub publish per tick);
- the heartbeat runs on its own timer, so an idle connection is pinged and a dead
  one is detected — previously the ping only fired while messages were arriving,
  which is exactly when it is not needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.deps import principal_from_token
from app.core.hk_bounds import is_in_hong_kong
from app.core.token_revocation import is_token_revoked
from app.models import DriverProfile, DriverStatus, Order, User

router = APIRouter(tags=["ws"])

# Close codes
WS_UNAUTHENTICATED = 4401
WS_FORBIDDEN = 4403
WS_UNKNOWN_ORDER = 4404
WS_CAPACITY = 4408


@router.websocket("/ws/trip/{order_id}")
async def trip_socket(
    ws: WebSocket,
    order_id: str,
    factory=Depends(get_session_factory),
):
    settings = get_settings()

    # --- SEC-14: authorize first, accept second. Everything below this block
    # runs before the 101 upgrade, so a rejected client holds no server socket.
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=WS_UNAUTHENTICATED)
        return
    try:
        user = principal_from_token(token)
    except HTTPException:
        await ws.close(code=WS_UNAUTHENTICATED)
        return

    # SEC-18: a revoked access token must not open a live channel either.
    # The client is the app's shared per-loop one, closed at shutdown — closing
    # it here would disconnect every other user of it on each WS handshake.
    with contextlib.suppress(Exception):
        if await is_token_revoked(ws.app.state.redis_factory(), user.id, user.issued_at):
            await ws.close(code=WS_UNAUTHENTICATED)
            return

    # --- resolve party, in a session of our OWN, released before the upgrade ---
    #
    # This deliberately does NOT take the request-scoped `Depends(get_session)`.
    # For a WebSocket that dependency is torn down when the handler returns —
    # which is when the socket closes, potentially hours later. The session, and
    # the pooled connection behind it, would therefore be held for the life of
    # every socket. The pool is `db_pool_size + db_max_overflow` = 30 per
    # process, while `ws_max_connections_total` is 2000: the 31st live trip
    # exhausts it, and from then on *every* request in the process stalls on
    # `pool_timeout` before failing. The per-tick work below already used
    # `factory()` for exactly this reason; the handshake was the leftover.
    #
    # The test suite could not see it: `tests/conftest.py` overrides both
    # `get_session` and `get_session_factory` with a NullPool engine, which has
    # no ceiling to hit. `tests/test_ws_module.py::test_handshake_releases_its_
    # connection` asserts it against the real pool instead.
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        await ws.close(code=WS_UNKNOWN_ORDER)
        return

    async with factory() as session:
        order = await session.get(Order, oid)
        if order is None:
            await ws.close(code=WS_UNKNOWN_ORDER)
            return
        # Read every value out as a plain scalar while the session is still
        # open. The ORM instances must not be touched after it closes.
        order_id_str = str(order.id)
        is_passenger = order.passenger_id == user.id
        driver_id_on_order = order.driver_id
        profile = (
            (await session.execute(select(DriverProfile).where(DriverProfile.user_id == user.id)))
            .scalars()
            .first()
        )
        profile_id = profile.id if profile is not None else None
        # P0-3: a deactivated account must not keep a live socket. One PK read;
        # a suspended driver re-checks per tick (DRIVER_NOT_ACTIVE).
        db_user = await session.get(User, user.id)
        user_is_active = db_user is not None and db_user.is_active

    is_driver = (
        profile_id is not None
        and driver_id_on_order is not None
        and driver_id_on_order == profile_id
    )
    if not (is_passenger or is_driver):
        await ws.close(code=WS_FORBIDDEN)
        return
    if not user_is_active:
        await ws.close(code=WS_FORBIDDEN)
        return
    party_kind = "driver" if is_driver else "passenger"

    # SEC-14: per-user and global caps, enforced before the upgrade.
    registry = ws.app.state.ws_registry
    if not await registry.acquire(user.id):
        await ws.close(code=WS_CAPACITY)
        return

    await ws.accept()
    ws.state.user_id = user.id

    hub = ws.app.state.trip_hub
    send_lock = asyncio.Lock()
    activity = {"at": time.monotonic()}
    # SEC-16: token bucket. Each tick is a DB UPDATE + commit + a Redis publish, so
    # the sustained rate is capped — but a small burst is allowed, because a driver
    # app that reconnects legitimately replays a few queued ticks at once.
    burst = max(1, settings.ws_tick_burst)
    rate = max(1, settings.ws_ticks_per_second)
    bucket = {"tokens": float(burst), "at": time.monotonic()}

    def allow_tick() -> bool:
        now = time.monotonic()
        bucket["tokens"] = min(burst, bucket["tokens"] + (now - bucket["at"]) * rate)
        bucket["at"] = now
        if bucket["tokens"] < 1:
            return False
        bucket["tokens"] -= 1
        return True

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(payload))
            activity["at"] = time.monotonic()

    async def handle_push(raw: str) -> None:
        if party_kind != "driver":
            await send({"type": "error", "code": "READ_ONLY"})
            return
        if not allow_tick():
            await send({"type": "error", "code": "RATE_LIMITED"})
            return
        # fresh session per tick: no cross-tick transaction state
        try:
            data = json.loads(raw)
            lat, lng = float(data["lat"]), float(data["lng"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            await send({"type": "error", "code": "BAD_MESSAGE"})
            return
        # Service area: a driver outside Hong Kong may not publish positions.
        # The check is the polygon one from `hk_bounds`, not the box this used
        # to carry — see that module for why the box admitted Shenzhen.
        if not is_in_hong_kong(lat, lng):
            await send({"type": "error", "code": "OUTSIDE_HK"})
            return
        async with factory() as ops:
            row = (
                await ops.execute(
                    select(DriverProfile.id, DriverProfile.status).where(
                        DriverProfile.user_id == user.id
                    )
                )
            ).first()
            if row is None or row.status != DriverStatus.ACTIVE:
                await send({"type": "error", "code": "DRIVER_NOT_ACTIVE"})
                return
            await hub.record_tick(ops, order_id_str, row.id, lat, lng)
        await send({"type": "ack", "lat": lat, "lng": lng})

    async def reader() -> None:
        while True:
            raw = await ws.receive_text()
            activity["at"] = time.monotonic()
            await handle_push(raw)

    heartbeat_s = max(1, settings.ws_heartbeat_s)

    async def heartbeat() -> None:
        """SEC-30: ping on a timer, independent of inbound traffic."""
        while True:
            await asyncio.sleep(heartbeat_s)
            await send({"type": "ping", "ts": time.time()})

    async def subscriber() -> None:
        async for message in hub.subscribe(order_id_str):
            if party_kind == "driver" and message.get("type") == "location":
                continue  # drivers get direct acks; no self-echo
            await send(message)

    async def watchdog() -> None:
        """SEC-14: reap a connection with no traffic in either direction."""
        while True:
            await asyncio.sleep(min(heartbeat_s, max(1, settings.ws_idle_timeout_s // 2)))
            if time.monotonic() - activity["at"] > settings.ws_idle_timeout_s:
                with contextlib.suppress(Exception):
                    await ws.close(code=1001)
                return

    tasks = [
        asyncio.create_task(heartbeat(), name="ws_heartbeat"),
        asyncio.create_task(subscriber(), name="ws_subscriber"),
        asyncio.create_task(watchdog(), name="ws_watchdog"),
    ]
    try:
        await reader()
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await registry.release(user.id)

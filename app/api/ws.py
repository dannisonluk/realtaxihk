"""Module D — WebSocket live-trip channel: /ws/trip/{order_id}.

Auth model (browser WS has no header channel):
  ws://host/ws/trip/{order_id}?token=<access JWT>

Authority rules:
- passenger (order owner): read-only subscriber;
- assigned driver: pusher + subscriber; pushes re-check driver status ACTIVE
  per tick (a suspended driver cannot keep streaming);
- everyone else: denied at handshake (4403).

Close codes: 4401 unauthenticated, 4403 forbidden, 4404 unknown order.

Protocol:
- driver sends {"lat": float, "lng": float} → receives {"type":"ack"} per
  processed tick, or {"type":"error","code": ...};
- subscriber pump delivers {"type":"location"} ticks and lifecycle events;
- the driver's own pump does NOT echo location ticks (direct ack instead),
  so the driver socket sees exactly one reply per sent tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
from app.core.deps import principal_from_token
from app.models import DriverProfile, DriverStatus, Order, User
from app.services.trip_service import TripHub

router = APIRouter(tags=["ws"])

WS_HK_BOUNDS = ((22.1, 22.6), (113.8, 114.5))


@router.websocket("/ws/trip/{order_id}")
async def trip_socket(
    ws: WebSocket,
    order_id: str,
    session: AsyncSession = Depends(get_session),
    factory=Depends(get_session_factory),
):
    token = ws.query_params.get("token")
    await ws.accept()
    if not token:
        await ws.close(code=4401)
        return
    try:
        user = principal_from_token(token)
    except HTTPException:
        await ws.close(code=4401)
        return
    ws.state.user_id = user.id

    # --- resolve party (request-scoped session, one read) ---
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        await ws.close(code=4404)
        return
    order = await session.get(Order, oid)
    if order is None:
        await ws.close(code=4404)
        return
    order_id_str = str(order.id)
    is_passenger = order.passenger_id == user.id
    profile = (
        (await session.execute(select(DriverProfile).where(DriverProfile.user_id == user.id)))
        .scalars()
        .first()
    )
    profile_id = profile.id if profile is not None else None
    driver_id_on_order = order.driver_id
    await session.rollback()  # release the implicit read transaction
    is_driver = (
        profile_id is not None
        and driver_id_on_order is not None
        and driver_id_on_order == profile_id
    )
    if not (is_passenger or is_driver):
        await ws.close(code=4403)
        return

    # P0-3: a deactivated account must not keep a live socket. One PK read;
    # a suspended driver re-checks per tick (DRIVER_NOT_ACTIVE).
    db_user = await session.get(User, user.id)
    if db_user is None or not db_user.is_active:
        await ws.close(code=4403)
        return
    party_kind = "driver" if is_driver else "passenger"

    hub = TripHub(ws.app.state.redis_factory)

    async def handle_push(raw: str) -> None:
        if party_kind != "driver":
            await ws.send_text(json.dumps({"type": "error", "code": "READ_ONLY"}))
            return
        # fresh session per tick: no cross-tick transaction state
        try:
            data = json.loads(raw)
            lat, lng = float(data["lat"]), float(data["lng"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            await ws.send_text(json.dumps({"type": "error", "code": "BAD_MESSAGE"}))
            return
        (lat_lo, lat_hi), (lng_lo, lng_hi) = WS_HK_BOUNDS
        if not (lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi):
            await ws.send_text(json.dumps({"type": "error", "code": "BAD_LOCATION"}))
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
                await ws.send_text(json.dumps({"type": "error", "code": "DRIVER_NOT_ACTIVE"}))
                return
            await hub.record_tick(ops, order_id_str, row.id, lat, lng)
        await ws.send_text(json.dumps({"type": "ack", "lat": lat, "lng": lng}))

    async def reader() -> None:
        while True:
            raw = await ws.receive_text()
            await handle_push(raw)

    heartbeat_s = get_settings().ws_heartbeat_s

    async def pump() -> None:
        last_ping = asyncio.get_event_loop().time()
        async for message in hub.subscribe(order_id_str):
            if party_kind == "driver" and message.get("type") == "location":
                continue  # drivers get direct acks; no self-echo
            await ws.send_text(json.dumps(message))
            now = asyncio.get_event_loop().time()
            if now - last_ping >= heartbeat_s:
                await ws.send_text(json.dumps({"type": "ping", "ts": now}))
                last_ping = now

    pump_task = asyncio.create_task(pump())
    try:
        await reader()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task

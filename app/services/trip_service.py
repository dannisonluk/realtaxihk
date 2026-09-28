"""Module D — trip location streaming: WS hub + tick pipeline + snapshots.

Channel layout (one Redis Pub/Sub channel per order):
  realtaxi:trip:{order_id}  — location ticks + lifecycle events

Driver pushes land here via the WS endpoint; the hub persists the tick to
PostGIS (driver profile), then fans out to all subscribers (passenger live
map, monitors). Lifecycle events (grab/cancel) publish on the same channel
so passenger apps can repaint without polling.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DriverProfile, Order


def channel_for(order_id: str) -> str:
    return f"realtaxi:trip:{order_id}"


class TripHub:
    """Per-order fan-out hub over Redis Pub/Sub (one subscriber per WS conn)."""

    def __init__(self, redis_factory):
        self._redis_factory = redis_factory

    async def publish(self, order_id: str, message: dict) -> None:
        rds = self._redis_factory()
        try:
            await rds.publish(channel_for(order_id), json.dumps(message))
        finally:
            await rds.aclose()

    async def subscribe(self, order_id: str):
        """Async-iterate channel messages until the caller stops consuming."""
        rds = self._redis_factory()
        pubsub = rds.pubsub()
        await pubsub.subscribe(channel_for(order_id))
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
        finally:
            try:
                await pubsub.unsubscribe(channel_for(order_id))
                await pubsub.aclose()
            except Exception:  # pragma: no cover — best-effort teardown
                pass

    async def record_tick(
        self,
        session: AsyncSession,
        order_id: str,
        driver_profile_id,
        lat: float,
        lng: float,
    ) -> None:
        """Persist tick (PostGIS) then fan out to the order channel."""
        now = datetime.now(timezone.utc)
        await session.execute(
            update(DriverProfile)
            .where(DriverProfile.id == driver_profile_id)
            .values(current_location=f"POINT({lng} {lat})", last_location_at=now)
        )
        await session.commit()
        await self.publish(
            order_id,
            {"type": "location", "lat": lat, "lng": lng, "ts": now.isoformat()},
        )


def trip_snapshot(order: Order, profile: DriverProfile | None) -> dict:
    """REST snapshot payload (reconnection fallback for the live map)."""
    lat = lng = None
    if profile is not None and profile.current_location is not None:
        # Geography columns read back as WKB; ST_AsText is applied in SQL,
        # so current_location arrives as WKT here when selected via snapshot query.
        wkt = getattr(profile, "_wkt", None)
        if wkt:
            lng_s, lat_s = wkt[6:-1].split()
            lng, lat = float(lng_s), float(lat_s)
    return {
        "order_id": str(order.id),
        "status": order.status.value if hasattr(order.status, "value") else str(order.status),
        "driver_id": str(profile.user_id) if profile is not None else None,
        "lat": lat,
        "lng": lng,
    }

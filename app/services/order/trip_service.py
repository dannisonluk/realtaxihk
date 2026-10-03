"""Module D — trip location streaming: WS hub + tick pipeline + snapshots.

Channel layout (one Redis Pub/Sub channel per order):
  realtaxi:trip:{order_id}  — location ticks + lifecycle events

Driver pushes land here via the WS endpoint; the hub persists the tick to
PostGIS (driver profile), then fans out to all subscribers (passenger live
map, monitors). Lifecycle events (grab/cancel) publish on the same channel
so passenger apps can repaint without polling.

SEC-14: the hub now owns ONE Redis client for the whole process. It used to call
the client factory per connection, so every WebSocket minted its own client and
connection pool — one account could open enough sockets to exhaust Redis's
`maxclients`, which takes out the grab locks, the geo index and Pub/Sub at once
(i.e. the entire dispatch path). Redis Pub/Sub still needs a dedicated
*connection* per subscriber, but those now come from a single bounded pool.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DriverProfile, Order


def channel_for(order_id: str) -> str:
    return f"realtaxi:trip:{order_id}"


class TripHub:
    """Per-order fan-out hub over Redis Pub/Sub, sharing one Redis client."""

    def __init__(self, redis):
        self._redis = redis

    async def publish(self, order_id: str, message: dict) -> None:
        await self._redis.publish(channel_for(order_id), json.dumps(message))

    async def subscribe(self, order_id: str):
        """Async-iterate channel messages until the caller stops consuming."""
        pubsub = self._redis.pubsub()
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
            # best-effort teardown — a dead connection must not break the pump
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.unsubscribe(channel_for(order_id))
                await pubsub.aclose()

    async def record_tick(
        self,
        session: AsyncSession,
        order_id: str,
        driver_profile_id,
        lat: float,
        lng: float,
    ) -> None:
        """Persist tick (PostGIS) then fan out to the order channel."""
        now = datetime.now(UTC)
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

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._redis.aclose()


class ConnectionRegistry:
    """Caps on concurrent WebSocket connections (SEC-14).

    In-process by design: it bounds what one worker can be made to hold. With
    several workers the effective limit multiplies, which is why the values are
    conservative and the Redis pool (not this counter) is the real backstop.
    """

    def __init__(self, max_per_user: int, max_total: int):
        self.max_per_user = max_per_user
        self.max_total = max_total
        self._lock = asyncio.Lock()
        self._per_user: dict[str, int] = {}
        self._total = 0

    async def acquire(self, user_id) -> bool:
        key = str(user_id)
        async with self._lock:
            if self._total >= self.max_total:
                return False
            if self._per_user.get(key, 0) >= self.max_per_user:
                return False
            self._per_user[key] = self._per_user.get(key, 0) + 1
            self._total += 1
            return True

    async def release(self, user_id) -> None:
        key = str(user_id)
        async with self._lock:
            remaining = self._per_user.get(key, 0) - 1
            if remaining > 0:
                self._per_user[key] = remaining
            else:
                self._per_user.pop(key, None)
            if self._total > 0:
                self._total -= 1

    @property
    def total(self) -> int:
        return self._total


def trip_snapshot(order: Order, profile: DriverProfile | None) -> dict:
    """REST snapshot payload (reconnection fallback for the live map).

    SEC-27: identifies the driver by *profile* id, not by the account UUID that
    signs their JWTs.
    """
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
        "driver_profile_id": str(profile.id) if profile is not None else None,
        "lat": lat,
        "lng": lng,
    }

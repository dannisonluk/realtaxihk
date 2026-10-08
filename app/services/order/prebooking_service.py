"""Pre-booking domain service.

Scheduled orders are created in PENDING and become visible to drivers at
``scheduled_pickup_at - PREBOOK_LEAD_MINUTES``. The background job moves due
rows into BROADCASTING, indexes them into Redis, and creates durable inbox rows
for matching online drivers. At the pickup time itself an unmatched row is
upgraded to a wider immediate broadcast; the state machine and Redis index are
unchanged, so this is recorded as an order event rather than a second status.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_redis
from app.models import (
    DriverBookingPreference,
    DriverNotification,
    DriverProfile,
    DriverStatus,
    Landmark,
    Order,
    OrderEventType,
    OrderKind,
    OrderStatus,
    PrebookState,
)
from app.services.order.geo_service import GeoService
from app.services.order.order_event_service import record_order_event

__all__ = ["PREBOOK_LEAD_MINUTES", "PrebookingBroadcaster", "PrebookingService"]

logger = logging.getLogger("realtaxihk.prebooking")

PREBOOK_LEAD_MINUTES = 30
PREBOOK_UPGRADE_RADIUS_KM = 6.0


class PrebookingService:
    """Create-time helper that stamps a scheduled order inside its own request."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare_scheduled(
        self,
        order: Order,
        scheduled_pickup_at: datetime,
        landmark: Landmark | None,
        original_dropoff_lat: float,
        original_dropoff_lng: float,
        original_dropoff_address: str,
    ) -> None:
        order.order_kind = OrderKind.SCHEDULED
        order.scheduled_pickup_at = scheduled_pickup_at
        order.prebook_visible_from = scheduled_pickup_at - timedelta(minutes=PREBOOK_LEAD_MINUTES)
        order.prebook_state = PrebookState.PENDING
        order.status = OrderStatus.CREATED
        if landmark is not None:
            order.dropoff_landmark_id = landmark.id
            order.dropoff_address = landmark.name_zh
            lat = _lat(landmark.location)
            lng = _lng(landmark.location)
            if lat is not None and lng is not None:
                order.dropoff_location = f"POINT({lng} {lat})"
            await record_order_event(
                self.session,
                order_id=order.id,
                event=OrderEventType.PREBOOK_LANDMARK_CHOSEN,
                actor_kind="passenger",
                payload={
                    "landmark_id": str(landmark.id),
                    "original_dropoff_lat": original_dropoff_lat,
                    "original_dropoff_lng": original_dropoff_lng,
                    "original_dropoff_address": original_dropoff_address,
                },
            )
        await self.session.flush()


class PrebookingBroadcaster:
    """Background job that releases due scheduled orders."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis=None) -> None:
        self.session_factory = session_factory
        self._redis = redis

    async def run_due(self, now: datetime | None = None, max_orders: int = 200) -> int:
        """Release due scheduled orders and upgrade pickup-time leftovers.

        Returns the number of orders that entered BROADCASTING this run.
        """
        now = now or datetime.now(UTC)
        async with self.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Order)
                        .where(
                            Order.order_kind == OrderKind.SCHEDULED,
                            Order.prebook_state == PrebookState.PENDING,
                            Order.prebook_visible_from.is_not(None),
                            Order.prebook_visible_from <= now,
                        )
                        .order_by(Order.prebook_visible_from.asc())
                        .limit(max_orders)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return 0
            changed = 0
            for order in rows:
                upgraded = (
                    order.scheduled_pickup_at is not None and order.scheduled_pickup_at <= now
                )
                order.status = OrderStatus.BROADCASTING
                order.prebook_state = PrebookState.BROADCASTING
                order.broadcast_radius_km = Decimal(
                    PREBOOK_UPGRADE_RADIUS_KM if upgraded else "3.0"
                )
                await record_order_event(
                    session,
                    order_id=order.id,
                    event=(
                        OrderEventType.PREBOOK_UPGRADED
                        if upgraded
                        else OrderEventType.PREBOOK_MATCHED
                    ),
                    from_status=OrderStatus.CREATED.value,
                    to_status=OrderStatus.BROADCASTING.value,
                    actor_kind="system",
                    payload={"radius_km": str(order.broadcast_radius_km)},
                )
                await self._notify_matching_drivers(session, order)
                point = self._order_point(order)
                if point is not None:
                    lat, lng = point
                    # Fail-open on Redis, matching `nearby_orders`: a Geo
                    # outage must not roll back the DB release (the broadcast
                    # still happened), and the geo sweep's reconcile pass will
                    # re-index what is missing once Redis is back.
                    try:
                        await GeoService(self._redis or get_redis()).index_order(
                            str(order.id), lat, lng
                        )
                    except Exception:
                        logger.exception(
                            "prebook release: redis index skipped for order %s",
                            order.id,
                        )
                changed += 1
            await session.commit()
            return changed

    async def _notify_matching_drivers(self, session: AsyncSession, order: Order) -> None:
        q = select(DriverProfile).where(
            DriverProfile.status == DriverStatus.ACTIVE,
            DriverProfile.is_online.is_(True),
            DriverProfile.taxi_type == order.taxi_type,
        )
        drivers = (await session.execute(q)).scalars().all()
        if not drivers:
            return
        profiles_by_id = {d.id: d for d in drivers}
        prefs = (
            (
                await session.execute(
                    select(DriverBookingPreference).where(
                        DriverBookingPreference.driver_profile_id.in_(profiles_by_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        pref_by_profile = {p.driver_profile_id: p for p in prefs}
        landmark = (
            await session.get(Landmark, order.dropoff_landmark_id)
            if order.dropoff_landmark_id
            else None
        )
        for driver in drivers:
            pref = pref_by_profile.get(driver.id)
            if not self._matches_preferences(order, pref, landmark):
                continue
            session.add(
                DriverNotification(
                    driver_profile_id=driver.id,
                    order_id=order.id,
                    kind="SCHEDULED",
                    headline_zh="預約訂單已釋出",
                    headline_en="Pre-booked order released",
                    body_zh=f"{order.pickup_address} → {order.dropoff_address} · "
                    f"預約 {order.scheduled_pickup_at:%H:%M}",
                    body_en=f"{order.pickup_address} → {order.dropoff_address} · "
                    f"scheduled {order.scheduled_pickup_at:%H:%M}",
                )
            )
        await session.flush()

    @staticmethod
    def _matches_preferences(
        order: Order,
        pref: DriverBookingPreference | None,
        landmark: Landmark | None,
    ) -> bool:
        """Category and availability matching for pre-book notifications.

        `preferred_origin_area` is stored and echoed through the API but is not
        consulted here today; only categories and availability gate the inbox.
        """
        if pref is None or not pref.categories:
            return True
        if landmark is None:
            return True
        if landmark.category.value not in pref.categories:
            return False
        if pref.available_from is None or pref.available_until is None:
            return True
        pickup_time = order.scheduled_pickup_at
        if pickup_time is None:
            return True
        pickup = pickup_time.time()
        return pref.available_from <= pickup < pref.available_until

    @staticmethod
    def _order_point(order: Order):
        # Same defensive decode used by `order_out`; no Shapely dependency.
        from app.services.order.order_service import _point_from_wkb

        return _point_from_wkb(order.pickup_location)


def _lat(value) -> float | None:
    from app.services.order.order_service import _point_lat

    return _point_lat(value)


def _lng(value) -> float | None:
    from app.services.order.order_service import _point_lng

    return _point_lng(value)

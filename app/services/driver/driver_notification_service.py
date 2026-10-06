"""Creates the durable driver inbox rows for premium/fixed-fare orders.

The same service is used by REST and, later, any realtime adapter: the row is
the source of truth, so a driver who was offline still sees what arrived.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money_str
from app.models import (
    DriverNotification,
    DriverProfile,
    DriverStatus,
    Order,
    OrderFareMode,
)

__all__ = ["DriverNotificationService"]


class DriverNotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def notify_order_created(self, order: Order, match: Any | None) -> None:
        """Add inbox rows for the two high-value order kinds.

        A fixed-fare order is already narrowed to the owner of the matching
        offer, so it notifies only that driver. A premium METER order is visible
        to every online ACTIVE driver of the same taxi type, mirroring the
        nearby broadcast semantics without leaking orders to irrelevant cabs.
        """
        if order.fare_mode == OrderFareMode.FIXED and match is not None:
            self._session.add(self._fixed_fare(order, match.offer))
            await self._session.flush()
            return

        if order.premium_destination_id is not None:
            profile_ids = (
                await self._session.execute(
                    select(DriverProfile.id).where(
                        DriverProfile.status == DriverStatus.ACTIVE,
                        DriverProfile.is_online.is_(True),
                        DriverProfile.taxi_type == order.taxi_type,
                    )
                )
            ).scalars().all()
            for profile_id in profile_ids:
                self._session.add(self._premium(order, profile_id))
            if profile_ids:
                await self._session.flush()

    def _fixed_fare(self, order: Order, offer: Any) -> DriverNotification:
        return DriverNotification(
            driver_profile_id=offer.driver_profile_id,
            order_id=order.id,
            kind="FIXED_FARE",
            headline_zh="一口價訂單已生成",
            headline_en="Fixed-fare order available",
            body_zh=self._fixed_body_zh(order, offer),
            body_en=self._fixed_body_en(order, offer),
        )

    def _fixed_body_zh(self, order: Order, offer: Any) -> str:
        price = money_str(offer.price_hkd)
        return f"{order.pickup_address} → {order.dropoff_address} · 一口價 HK${price}"

    def _fixed_body_en(self, order: Order, offer: Any) -> str:
        price = money_str(offer.price_hkd)
        return f"{order.pickup_address} → {order.dropoff_address} · Fixed HK${price}"

    def _premium(self, order: Order, profile_id: Any) -> DriverNotification:
        meta = order.premium_destination_json or {}
        name_zh = str(meta.get("name_zh") or "Premium 目的地")
        name_en = str(meta.get("name_en") or "premium destination")
        price = money_str(order.estimated_total_hkd)
        return DriverNotification(
            driver_profile_id=profile_id,
            order_id=order.id,
            kind="PREMIUM",
            headline_zh=f"新訂單：{name_zh}",
            headline_en=f"New premium order: {name_en}",
            body_zh=f"{order.pickup_address} → {order.dropoff_address} · 約 HK${price}",
            body_en=(
                f"{order.pickup_address} → {order.dropoff_address} · "
                f"Est. HK${price}"
            ),
        )

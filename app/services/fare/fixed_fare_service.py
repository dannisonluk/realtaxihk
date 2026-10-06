"""Fixed-fare matching service (一口價).

Driver offers + passenger request -> a frozen fixed fare, or nothing.

The invariant that matters is **money freezes at order creation**. This module
returns a match description; `OrderService.create` stores the chosen offer id
and the derived amounts on the order. Once stored, neither the driver nor the
platform can change the price for that order.

Fee rule (config, disclosed to both sides):
- passenger_price = offer.price + platform_fee
- platform_fee = max(ROUND_HALF_UP(offer.price * fee_percent / 100), min_fee)
- match only when passenger_price <= meter_estimate (otherwise the fixed fare
  is not the better deal and the order stays METER)
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.region import is_valid_area
from app.models import FixedOfferStatus, FixedPriceOffer, Order, PremiumDestination

__all__ = [
    "FixedFareMatch",
    "FixedFareService",
    "calculate_platform_fee",
    "offer_matches_order_route",
]


def calculate_platform_fee(offer_price: Decimal) -> Decimal:
    """The platform's disclosed service fee on a fixed fare.

    ROUND_HALF_UP everywhere, matching the project's money rule (never the
    Decimal default ROUND_HALF_EVEN). The floor keeps small offers viable to
    broker; the percentage makes the fee scale with the trip.
    """
    s = get_settings()
    percent = Decimal(s.fixed_fare_fee_percent)
    fee = (offer_price * percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(fee, Decimal(s.fixed_fare_min_fee_hkd))


@dataclass(frozen=True)
class FixedFareMatch:
    offer: FixedPriceOffer
    platform_fee_hkd: Decimal
    passenger_price_hkd: Decimal
    meter_estimate_hkd: Decimal


def offer_matches_order_route(
    offer: FixedPriceOffer, order: Order, meter_estimate: Decimal
) -> bool:
    """Whether a standing offer covers this order's route and is competitive.

    Route matching is deliberately permissive on the pickup side: a NULL pickup
    area means the driver will do the journey from anywhere.
    """
    if offer.status != FixedOfferStatus.ACTIVE.value:
        return False

    if offer.premium_destination_id is not None:
        if order.premium_destination_id != offer.premium_destination_id:
            return False
    elif offer.destination_area is not None:
        if order.destination_area != offer.destination_area:
            return False
    else:
        return False

    if offer.pickup_area is not None and order.pickup_area != offer.pickup_area:
        return False

    fee = calculate_platform_fee(offer.price_hkd)
    return offer.price_hkd + fee <= meter_estimate


class FixedFareService:
    """Matching and offer CRUD. One class so the rules live beside the rows."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def match_order(
        self,
        order: Order,
        meter_estimate: Decimal,
        *,
        pickup_area: str | None,
    ) -> FixedFareMatch | None:
        """Pick the cheapest ACTIVE offer that covers `order`'s route.

        The order must already carry `destination_area` and
        `premium_destination_id` (computed in `OrderService.create`). Returns
        None when no offer is competitive, leaving the order in METER mode.
        """
        if not get_settings().fixed_fare_enabled:
            return None

        rows = (
            (
                await self.session.execute(
                    select(FixedPriceOffer).where(
                        FixedPriceOffer.status == FixedOfferStatus.ACTIVE.value,
                        FixedPriceOffer.driver_profile_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None

        best: FixedFareMatch | None = None
        for offer in rows:
            if offer_matches_order_route(offer, order, meter_estimate):
                fee = calculate_platform_fee(offer.price_hkd)
                passenger = offer.price_hkd + fee
                if best is None or passenger < best.passenger_price_hkd:
                    best = FixedFareMatch(
                        offer=offer,
                        platform_fee_hkd=fee,
                        passenger_price_hkd=passenger,
                        meter_estimate_hkd=meter_estimate,
                    )
        return best

    async def create_offer(
        self,
        *,
        driver_profile_id: uuid_mod.UUID,
        destination_area: str | None,
        premium_destination_id: uuid_mod.UUID | None,
        pickup_area: str | None,
        price_hkd: Decimal,
    ) -> FixedPriceOffer:
        """Create an ACTIVE fixed-fare offer for the driver.

        At least one destination dimension must be present; a duplicate ACTIVE
        offer for the same route raises ValueError (the migration's partial
        unique index is the hard backstop).
        """
        if not destination_area and premium_destination_id is None:
            raise ValueError("an offer needs a destination area or premium destination")
        # Area codes are validated against the closed set in `app.core.region`.
        # An offer is matched against `Order.destination_area` / `pickup_area`,
        # which the region helpers can only ever set to one of those five codes
        # — so an offer naming anything else is not a narrow offer, it is an
        # offer that can never match. A typo that silently matches nothing is
        # exactly what `region.ALL_AREAS` exists to turn into a 422.
        if destination_area is not None and not is_valid_area(destination_area):
            raise ValueError(f"unknown destination area: {destination_area}")
        if pickup_area is not None and not is_valid_area(pickup_area):
            raise ValueError(f"unknown pickup area: {pickup_area}")
        # Checked here rather than left to the FK: a dangling id fails the
        # INSERT, and the bare `except IntegrityError` below would report that
        # as a duplicate route. Two different failures must not share one
        # message.
        if (
            premium_destination_id is not None
            and (await self.session.get(PremiumDestination, premium_destination_id)) is None
        ):
            raise ValueError(f"unknown premium destination: {premium_destination_id}")
        offer = FixedPriceOffer(
            driver_profile_id=driver_profile_id,
            destination_area=destination_area,
            premium_destination_id=premium_destination_id,
            pickup_area=pickup_area,
            price_hkd=price_hkd,
            status=FixedOfferStatus.ACTIVE.value,
        )
        self.session.add(offer)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError("an active offer for this route already exists") from exc
        return offer


async def find_by_id(session: AsyncSession, offer_id: uuid_mod.UUID) -> FixedPriceOffer | None:
    return await session.get(FixedPriceOffer, offer_id)

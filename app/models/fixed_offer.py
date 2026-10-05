"""Fixed-fare offers (一口價) — a driver's standing flat-price commitment.

A driver publishes an offer: for a route (a premium destination and/or a
destination area, optionally constrained by a pickup area) they will do the
trip for `price_hkd`. The passenger-facing price is the offer price plus the
platform service fee; the spread is the platform's revenue for brokering the
fixed fare — see `FixedFareService.match`.

The offer is a *standing* commitment, so an order that matches it freezes a
reference to it. The driver cannot change the price after the order exists —
the fare snapshot is the authority (`Cap. 374D` reasoning in `order_service`).

One ACTIVE offer per (driver, route). The uniqueness is expressed as a partial
index in the migration because the route fields are nullable and a plain
UNIQUE column would treat two NULLs as distinct rows.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

if TYPE_CHECKING:
    from app.models.user import DriverProfile

__all__ = ["FixedOfferStatus", "FixedPriceOffer"]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FixedOfferStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class FixedPriceOffer(Base):
    __tablename__ = "fixed_price_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), index=True
    )
    # Route = destination + optional pickup constraint.
    # destination_area is the coarse server label (AIRPORT / KOWLOON / ...);
    # premium_destination_id pins a geofenced place (Airport T1, Cathay City).
    # At least one destination dimension is required (service check).
    destination_area: Mapped[str | None] = mapped_column(String(24), nullable=True)
    premium_destination_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("premium_destinations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pickup_area: Mapped[str | None] = mapped_column(String(24), nullable=True)
    price_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(16), default=FixedOfferStatus.ACTIVE, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    driver_profile: Mapped[DriverProfile] = relationship(back_populates="fixed_offers")

    __table_args__ = (
        Index(
            "uq_fixed_offer_active_route",
            "driver_profile_id",
            text("COALESCE(premium_destination_id, '00000000-0000-0000-0000-000000000000')"),
            text("COALESCE(destination_area, '')"),
            text("COALESCE(pickup_area, '')"),
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

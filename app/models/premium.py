"""Premium destinations and driver attribute tables.

Premium destinations are admin-managed places (e.g. Cathay City, Airport T1/T2)
that deserve an avatar pin on the map and can later anchor driver flat-fare
offers. Driver attributes are the payment methods and in-car environment
capabilities that a passenger must see before accepting a trip.

Bounded context: these rows are public metadata and driver self-descriptions,
not passenger identity or money. Order-level requirements (animal size, silent
ride, no radio/music) live on ``orders.requirements_json`` so they freeze with
the fare snapshot.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base import Base

__all__ = [
    "DestinationStatus",
    "DriverPaymentMethod",
    "PaymentMethod",
    "PremiumDestination",
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DestinationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    HIDDEN = "HIDDEN"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    OCTOPUS = "OCTOPUS"
    CARD = "CARD"
    ALIPAY = "ALIPAY"
    WECHAT_PAY = "WECHAT_PAY"
    TAP_AND_GO = "TAP_AND_GO"


class PremiumDestination(Base):
    __tablename__ = "premium_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_zh: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Numeric(9, 6))
    lng: Mapped[float] = mapped_column(Numeric(9, 6))
    radius_m: Mapped[int] = mapped_column(default=200)
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=DestinationStatus.ACTIVE, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DriverPaymentMethod(Base):
    __tablename__ = "driver_payment_methods"

    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    method: Mapped[str] = mapped_column(String(24), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("driver_profile_id", "method", name="uq_driver_payment_method"),
    )

"""In-app driver alerts for premium-destination and fixed-fare dispatch.

These rows are the durable inbox behind the badge. External push (WhatsApp /
FCM / Google Maps) is intentionally out of scope; the same row is consumed by
the REST list and can later feed a WebSocket or push adapter without changing
the contract.

Why a table instead of Redis: Redis is a transient index in this project, and a
driver opening the app after a disconnect still needs to see what arrived while
they were offline. Postgres is the source of truth for the inbox.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base import Base

__all__ = ["DriverNotification", "DriverNotificationKind"]


class DriverNotificationKind(str, enum.Enum):
    PREMIUM = "PREMIUM"
    FIXED_FARE = "FIXED_FARE"
    SCHEDULED = "SCHEDULED"


class DriverNotification(Base):
    __tablename__ = "driver_notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    headline_zh: Mapped[str] = mapped_column(String(120), nullable=False)
    headline_en: Mapped[str] = mapped_column(String(120), nullable=False)
    body_zh: Mapped[str] = mapped_column(String(500), nullable=False)
    body_en: Mapped[str] = mapped_column(String(500), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "ix_driver_notifications_profile_created",
            "driver_profile_id",
            "created_at",
        ),
    )

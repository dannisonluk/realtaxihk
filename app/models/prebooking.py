"""Pre-booking models: landmarks, driver booking preferences, and order enums.

`OrderKind` and `PrebookState` are defined here because they are the pre-booking
wire contract with mobile; `Order` imports them and stores their string values.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Time, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base


class OrderKind(str, Enum):
    ON_DEMAND = "ON_DEMAND"
    SCHEDULED = "SCHEDULED"


class PrebookState(str, Enum):
    PENDING = "PENDING"
    BROADCASTING = "BROADCASTING"
    MATCHED = "MATCHED"
    EXPIRED = "EXPIRED"


class LandmarkCategory(str, Enum):
    AIRPORT = "AIRPORT"
    VENUE = "VENUE"
    HOSPITAL = "HOSPITAL"
    BORDER = "BORDER"
    WATERFRONT = "WATERFRONT"
    THEME_PARK = "THEME_PARK"
    OFFICE = "OFFICE"
    MALL = "MALL"
    OTHER = "OTHER"


class Landmark(Base):
    """A curated destination passengers can pre-book to."""

    __tablename__ = "landmarks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[LandmarkCategory] = mapped_column(
        SAEnum(
            LandmarkCategory,
            name="ck_landmarks_category",
            native_enum=False,
            create_constraint=True,
            length=24,
        ),
        nullable=False,
    )
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    radius_m: Mapped[int] = mapped_column(
        Integer, nullable=False, default=800, server_default="800"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DriverBookingPreference(Base):
    """Driver opt-in categories and hours for pre-booked airport/venue work."""

    __tablename__ = "driver_booking_preferences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    driver_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    categories: Mapped[list[str] | None] = mapped_column(ARRAY(String(24)), nullable=True)
    preferred_origin_area: Mapped[str | None] = mapped_column(String(120), nullable=True)
    available_from: Mapped[Time | None] = mapped_column(Time, nullable=True)
    available_until: Mapped[Time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    driver_profile = relationship("DriverProfile", back_populates="booking_preference")


__all__ = [
    "DriverBookingPreference",
    "Landmark",
    "LandmarkCategory",
    "OrderKind",
    "PrebookState",
]

"""Recurring rides — a passenger's standing weekly trip schedule.

Design
------
A recurring ride is a *template*, not an order. It stores the same payload a
one-off `/api/v1/orders` POST would carry (minus coordinates validation which
happens at mint time), plus a DB-side `next_run_at` cursor. A background job
(`RecurringService.run_due`) mints **one** order per due ride per sweep, then
advances the cursor to the next occurrence.

Why a template and not a batch of future orders:
- every order freezes its own fare snapshot at creation; pre-minting would
  freeze fares before the trip and break the platform's estimator contract;
- one minted order is one broadcast; a month of pre-minted orders would spam
  the geo index and let a stale template flood the market.

Cap. 374D positioning is unchanged: the platform remains an information
intermediary. A recurring ride is a passenger-side convenience; each minted
order is still an independent BROADCASTING order that a driver accepts one at
a time.

Status lifecycle: ACTIVE -> PAUSED (driver/template owner) -> ACTIVE;
ACTIVE -> CANCELLED is terminal. CANCELLED rows are retained for audit.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base import Base


class RecurringStatus(str, enum.Enum):
    """Lifecycle of a recurring ride template."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class RecurringFrequency(str, enum.Enum):
    """How often a recurring ride fires. WEEKLY is the only value today; the
    enum exists so a future DAILY/EVERY_OTHER_WEEK lands as data, not as a
    schema rewrite."""

    WEEKLY = "WEEKLY"


def _now() -> datetime:
    return datetime.now(UTC)


class RecurringRide(Base):
    """One passenger's standing weekly trip template."""

    __tablename__ = "recurring_rides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    passenger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RecurringStatus] = mapped_column(
        SAEnum(
            RecurringStatus,
            name="ck_recurring_rides_status",
            native_enum=False,
            create_constraint=True,
            # `length` is not decorative. Without it SQLAlchemy derives the
            # column width from the longest member (`CANCELLED` = 9), which no
            # longer matches the `VARCHAR(16)` the migration created — and
            # `alembic check` then reports that difference as drift on every
            # run, burying the one item that would actually be new.
            length=16,
        ),
        default=RecurringStatus.ACTIVE,
        nullable=False,
    )
    frequency: Mapped[RecurringFrequency] = mapped_column(
        SAEnum(
            RecurringFrequency,
            name="ck_recurring_rides_frequency",
            native_enum=False,
            create_constraint=True,
            # Same reason: `WEEKLY` is 6 characters, the column is 16.
            length=16,
        ),
        default=RecurringFrequency.WEEKLY,
        nullable=False,
    )
    # ISO weekday: 1 = Monday .. 7 = Sunday.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # "HH:MM" in Asia/Hong_Kong, 24h.
    scheduled_time: Mapped[str] = mapped_column(String(5), nullable=False)
    # The mintable order payload (OrderCreateIn shape, sans passenger).
    template_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Audit trail: which real order seeded this template, and which order was
    # last minted from it.
    source_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    last_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    # DB cursor for the minting job; advanced to the next occurrence after a
    # successful mint, or pushed forward one period when a mint is skipped so a
    # wedged template cannot fire repeatedly every sweep.
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    __table_args__ = (
        Index("ix_recurring_rides_status_next_run", "status", "next_run_at"),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_recurring_rides_weekday"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RecurringRide {self.id} passenger={self.passenger_id} {self.status.value}>"


__all__ = ["RecurringFrequency", "RecurringRide", "RecurringStatus"]

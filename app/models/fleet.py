"""Fleets (車隊) — licensed operators, their rosters and weekly settlement.

Split out of the original single `app/models/__init__.py`; every public name is
re-exported from `app.models`, so no call site changed.

Bounded context: an operator's standing with the platform, who is on its roster
and when, and the record of each weekly invoice. The money itself is *not* here
— per-driver charges land in `LedgerEntry` (`app.models.user`), and this module
only aggregates what was collected. That asymmetry is deliberate: the ledger is
the authoritative money, these rows are what the operator is shown.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

__all__ = [
    "Fleet",
    "FleetMemberRole",
    "FleetMemberStatus",
    "FleetMembership",
    "FleetSettlementRun",
    "FleetStatus",
]


def _uuid() -> uuid.UUID:
    """Per-column UUID default.

    Kept identical to the helper the other model modules define: all of them
    call `uuid.uuid4()`, and the column default must stay a plain function
    reference so the generated DDL is unchanged by this package split.
    """
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FleetStatus(str, enum.Enum):
    """A licensed operator's standing with the platform."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISSOLVED = "DISSOLVED"


class FleetMemberRole(str, enum.Enum):
    """Who speaks for the fleet. Only OWNER and MANAGER may change the roster."""

    OWNER = "OWNER"
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"


class FleetMemberStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"
    REMOVED = "REMOVED"


class Fleet(Base):
    """A taxi fleet (的士車隊) — a licensed operator with many drivers.

    HK fleets are licensed operators, not self-service groups: the Transport
    Department grants the fleet licence and the platform onboards the operator,
    so a fleet is created by an admin and never by a driver. Drivers join by
    being added to the roster.

    `weekly_fee_discount_percent` is the platform's volume arrangement. It is
    applied to each member's weekly service fee by `FleetSettlementService`, and
    it is the reason a fleet member must **not** also be charged by the
    platform-wide weekly run — see `SettlementService.run_weekly`.
    """

    __tablename__ = "fleets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # Transport Department fleet licence number.
    license_no: Mapped[str] = mapped_column(String(40), unique=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    contact_name: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[FleetStatus] = mapped_column(
        SAEnum(FleetStatus, name="fleet_status", native_enum=False),
        default=FleetStatus.ACTIVE,
        index=True,
    )
    # Volume discount on the weekly service fee, 0-100.
    weekly_fee_discount_percent: Mapped[object] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list[FleetMembership]] = relationship(
        back_populates="fleet", cascade="all, delete-orphan"
    )


class FleetMembership(Base):
    """A driver's place on a fleet roster.

    Rows are never deleted on leave: `status` moves to LEFT/REMOVED and
    `left_at` is stamped, so the roster history — and therefore which fleet a
    driver was billed under in a past week — stays auditable.
    """

    __tablename__ = "fleet_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="CASCADE"), index=True
    )
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), index=True
    )
    member_role: Mapped[FleetMemberRole] = mapped_column(
        SAEnum(FleetMemberRole, name="fleet_member_role", native_enum=False),
        default=FleetMemberRole.MEMBER,
    )
    status: Mapped[FleetMemberStatus] = mapped_column(
        SAEnum(FleetMemberStatus, name="fleet_member_status", native_enum=False),
        default=FleetMemberStatus.ACTIVE,
        index=True,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    fleet: Mapped[Fleet] = relationship(back_populates="memberships")

    __table_args__ = (
        # A driver may be on at most ONE active roster. Without this a driver
        # could be added to two fleets and be billed by both.
        Index(
            "uq_fleet_active_member_per_driver",
            "driver_profile_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class FleetSettlementRun(Base):
    """The record of one fleet's weekly settlement (車隊結算).

    One row per (fleet, ISO week). A re-run updates the row rather than
    appending, so the row always describes what the fleet was actually billed
    for that week.

    The per-driver ledger entries carry the authoritative money; this row is the
    aggregate the operator is shown and the platform reconciles against. The
    counters mirror `SettlementService.run_weekly`, including `tampered` — a
    non-zero there means a ledger reference was held by a different entry and
    the fee was deliberately not collected (`SEC-13`).
    """

    __tablename__ = "fleet_settlement_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(12))  # ISO week, e.g. 2026-W40
    fee_hkd: Mapped[object] = mapped_column(Numeric(10, 2))  # per member, after discount
    discount_percent: Mapped[object] = mapped_column(Numeric(5, 2))
    member_count: Mapped[int] = mapped_column(BigInteger)
    charged: Mapped[int] = mapped_column(BigInteger, default=0)
    skipped: Mapped[int] = mapped_column(BigInteger, default=0)
    failed: Mapped[int] = mapped_column(BigInteger, default=0)
    tampered: Mapped[int] = mapped_column(BigInteger, default=0)
    collected_hkd: Mapped[object] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("fleet_id", "period", name="uq_fleet_settlement_period"),)

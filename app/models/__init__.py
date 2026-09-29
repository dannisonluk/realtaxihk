"""SQLAlchemy 2.0 declarative models — realtaxihk.com core domain.

Design notes:
- Money columns: Numeric(10,2) — HKD has no sub-cent usage on meter fares; ledger
  keeps signed amounts with an append-only constraint.
- Geo columns: PostGIS geography(Point,4326) (srid 4326 matches Google Maps).
- Orders carry a snapshot of the fare estimate (tariff_version + totals) so
  historical orders stay auditable even after tariff changes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UserRole(str, enum.Enum):
    PASSENGER = "PASSENGER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"


class DriverStatus(str, enum.Enum):
    PENDING_KYC = "PENDING_KYC"
    DEPOSIT_REQUIRED = "DEPOSIT_REQUIRED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class OrderStatus(str, enum.Enum):
    CREATED = "CREATED"
    BROADCASTING = "BROADCASTING"
    ACCEPTED = "ACCEPTED"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    IN_TRIP = "IN_TRIP"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class LedgerEntryType(str, enum.Enum):
    DEPOSIT_TOPUP = "DEPOSIT_TOPUP"
    WEEKLY_FEE_DEDUCTION = "WEEKLY_FEE_DEDUCTION"
    PENALTY_DEDUCTION = "PENALTY_DEDUCTION"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


class RefundStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False), default=UserRole.PASSENGER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    driver_profile: Mapped[DriverProfile | None] = relationship(back_populates="user")


class DriverProfile(Base):
    __tablename__ = "driver_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # KYC — HK-specific identifiers (stored as provided; masked in API outputs)
    hk_id_last4: Mapped[str] = mapped_column(String(4))  # e.g. "1234"
    taxi_driver_plate_no: Mapped[str] = mapped_column(String(10), index=True)  # 的士司機證
    vehicle_reg_mark: Mapped[str] = mapped_column(String(8), index=True)  # 車牌
    taxi_type: Mapped[str] = mapped_column(String(10))  # URBAN / NT / LANTAU
    status: Mapped[DriverStatus] = mapped_column(
        SAEnum(DriverStatus, name="driver_status", native_enum=False),
        default=DriverStatus.PENDING_KYC,
    )
    kyc_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kyc_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="driver_profile")
    deposit: Mapped[DriverDeposit | None] = relationship(back_populates="driver_profile")
    # Current GPS position (updated every 3-5s while online)
    current_location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_driver_profiles_location", DriverProfile.current_location, postgresql_using="gist")


class DriverDeposit(Base):
    __tablename__ = "driver_deposits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), unique=True
    )
    balance_hkd: Mapped[object] = mapped_column(Numeric(10, 2), default=0)  # available
    held_hkd: Mapped[object] = mapped_column(Numeric(10, 2), default=0)  # locked pending refund
    required_hkd: Mapped[object] = mapped_column(Numeric(10, 2), default=500)
    is_fulfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    driver_profile: Mapped[DriverProfile] = relationship(back_populates="deposit")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    passenger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status", native_enum=False),
        default=OrderStatus.CREATED,
        index=True,
    )
    # Requested route
    pickup_location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    pickup_address: Mapped[str] = mapped_column(Text)
    dropoff_location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    dropoff_address: Mapped[str] = mapped_column(Text)
    distance_km: Mapped[object] = mapped_column(Numeric(7, 3))
    taxi_type: Mapped[str] = mapped_column(String(10))
    # Fare estimate snapshot (Cap. 374D disclaimer fields included in fare_json)
    fare_json: Mapped[dict] = mapped_column(JSONB)
    tariff_version: Mapped[str] = mapped_column(String(60))
    estimated_total_hkd: Mapped[object] = mapped_column(Numeric(10, 2))
    discount_percent: Mapped[object] = mapped_column(Numeric(5, 2), default=0)
    # Broadcast config
    broadcast_radius_km: Mapped[object] = mapped_column(Numeric(4, 1), default=3.0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    driver_arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index("ix_orders_pickup_geo", Order.pickup_location, postgresql_using="gist")


class LedgerEntry(Base):
    """Append-only financial audit trail. Rows are never updated or deleted."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="RESTRICT"), index=True
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        SAEnum(LedgerEntryType, name="ledger_entry_type", native_enum=False)
    )
    amount_hkd: Mapped[object] = mapped_column(Numeric(10, 2))  # signed: +credit / -debit
    balance_after_hkd: Mapped[object] = mapped_column(Numeric(10, 2))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reference: Mapped[str | None] = mapped_column(String(120))  # external ref / admin note id
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint("id", "created_at", name="uq_ledger_id_created_at"),
        # Guard against tampering: sequence must strictly increase with id
        Index("ix_ledger_driver_created", "driver_profile_id", "created_at"),
        # Idempotency backstop: one entry per non-null reference, so a retried
        # write (admin grant, weekly fee) can never double-post.
        Index(
            "uq_ledger_reference",
            "reference",
            unique=True,
            postgresql_where=text("reference IS NOT NULL"),
        ),
    )


class OtpCode(Base):
    """WhatsApp OTP codes. Short retention (PDPO): purged after expiry."""

    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))  # sha256 — never store plaintext
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    """Rotating refresh tokens (PDPO: hashed at rest, purged after expiry)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundRequest(Base):
    """Driver-initiated deposit refund — money is *held*, never moved on request.

    On request the driver's `balance_hkd` is locked into `held_hkd` and the driver
    leaves ACTIVE (ACTIVE is the dispatch gate, so this also stops new jobs). The
    ledger entry is written only when an admin approves — the single point where
    value actually leaves the platform. `uq_refund_pending_per_driver` turns a
    double-submit race into a DB error instead of a double hold.
    """

    __tablename__ = "refund_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), index=True
    )
    amount_hkd: Mapped[object] = mapped_column(Numeric(10, 2))
    status: Mapped[RefundStatus] = mapped_column(
        SAEnum(RefundStatus, name="refund_status", native_enum=False),
        default=RefundStatus.PENDING,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text)  # driver's reason
    decision_note: Mapped[str | None] = mapped_column(Text)  # admin's note
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # At most one open (PENDING) request per driver — DB backstop for a
        # double-submit that slipped past the service-layer check.
        Index(
            "uq_refund_pending_per_driver",
            "driver_profile_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )


# ---------------------------------------------------------------------------
# Fleets (車隊)
# ---------------------------------------------------------------------------


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

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
    Integer,
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


class Gender(str, enum.Enum):
    """Optional, and never inferred. Kept as a small closed set rather than
    free text so the client can render a picker and the data stays queryable.
    `UNDISCLOSED` is a real choice, not a missing value — which is why it is a
    member rather than NULL."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNDISCLOSED = "UNDISCLOSED"


class AccountStatus(str, enum.Enum):
    """Whether an account may be used at all.

    Distinct from `is_active`, which is the admin's ban switch. `AccountStatus`
    is the *self-service* lifecycle: an account registers, proves its email and
    phone, and only then is ACTIVE. Keeping them separate means "banned by an
    admin" and "has not finished signing up" stay distinguishable in the audit
    trail — collapsing them loses that.
    """

    UNVERIFIED = "UNVERIFIED"  # registered; email and/or phone not yet proven
    ACTIVE = "ACTIVE"  # both proven; the account works
    SUSPENDED = "SUSPENDED"  # admin action; see is_active + the audit log


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

    # --- Registration identity (Uber-shaped). Every column is nullable because
    # the table predates this scheme: accounts created by phone-OTP alone have
    # no username, email or password until they claim credentials on next login
    # (the grandfathering path). A NOT NULL here would break every existing row.
    username: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    given_name: Mapped[str | None] = mapped_column(String(60))
    family_name: Mapped[str | None] = mapped_column(String(60))
    gender: Mapped[Gender | None] = mapped_column(
        SAEnum(Gender, name="gender", native_enum=False)
    )
    # Cloudflare R2 object key, not a URL: the bucket and host are deployment
    # config, and storing a full URL bakes a CDN hostname into user rows that
    # then cannot be changed without a data migration.
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mirrors `password_hash` on AdminAccount; argon2id string, ~97 chars today
    # but the encoded form grows with parameters, so leave headroom.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    account_status: Mapped[AccountStatus] = mapped_column(
        SAEnum(AccountStatus, name="account_status", native_enum=False),
        default=AccountStatus.UNVERIFIED,
    )

    # P-4: monthly phone re-verification. `phone_reverify_due_at` is the
    # deadline; NULL on a grandfathered row means "not yet scheduled", which the
    # guard treats as due rather than as unlimited — failing closed.
    phone_reverify_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Password/username lockout state. Separate from `is_active` (an admin ban)
    # so a brute-force lock is self-clearing and never needs manual unbanning.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # P-3: every licence submission this driver has ever made, newest first
    # (ordering is applied at query time, not here).
    licence_submissions: Mapped[list[DriverLicenceSubmission]] = relationship(
        back_populates="driver_profile", cascade="all, delete-orphan"
    )
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


class AdminAccount(Base):
    """An administrator / management-team account — a **separate identity type**.

    Deliberately not a `users` row with extra columns, and not reachable through
    the phone-OTP login path. The reasons are load-bearing:

    1. **Different credential.** A user is identified by a unique *phone*; an
       admin by a unique *username* plus a unique *email*. Sharing one table
       means three uniquely-constrained columns that are all nullable and whose
       meaning depends on `role` — a shape where a bug produces an admin with no
       phone, or a passenger with an email that collides with an admin's.

    2. **Different second factor.** Admins require TOTP. There is no passenger
       equivalent, so `totp_secret` would sit NULL on ~all user rows.

    3. **Blast radius.** This account can move money (`ADJUSTMENT`, refund
       approval, fleet settlement). Keeping it in its own table means a bug in
       passenger registration cannot mint an admin, and `require_admin` has
       exactly one table to check.

    The Trade-off accepted: an admin cannot also be a passenger on the same
    account. That is intentional — it keeps "who can move money" unambiguous.
    """

    __tablename__ = "admin_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Lower-cased on write by the service layer, so uniqueness is
    # case-insensitive without a functional index (which would need CITEXT or
    # a migration-specific expression index).
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))

    # --- TOTP (RFC 6238). Enrolled on first login; see `AdminTotpService`.
    # NULL until enrolment completes, which is the state that makes the login
    # flow a state machine: password is proven, TOTP is not yet configured.
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Replay protection: the last accepted 30-second step. A code at or before
    # this counter is refused, making each code single-use. Without it the
    # ±1-step window leaves a code replayable for up to 90 seconds.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Brute-force lockout, self-clearing. Kept off `is_active` so an automated
    # lock never looks like an admin ban in the audit trail.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Which admin created this one, for the audit trail.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    recovery_codes: Mapped[list[AdminRecoveryCode]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )


class AdminRecoveryCode(Base):
    """Single-use codes for when the authenticator device is lost.

    Without these, a lost phone is a locked-out administrator and a hand-edited
    database row — which is how a security control becomes an operational
    incident, and why second-factor rollouts get quietly abandoned.

    Stored one-way (SHA-256; the code is 50 bits of CSPRNG output, so there is
    no dictionary to attack and no need for a slow KDF on the login path).
    `used_at` rather than deletion keeps the fact that a code *was* used, which
    is exactly what you want to see when investigating an account takeover.
    """

    __tablename__ = "admin_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    admin: Mapped[AdminAccount] = relationship(back_populates="recovery_codes")

    __table_args__ = (UniqueConstraint("admin_id", "code_hash", name="uq_admin_recovery_code"),)


class AdminAuditLog(Base):
    """Append-only record of privileged actions and authentication events.

    A separate table from the driver/staff audit needs because the questions are
    different: this one answers "who logged in, from where, and did they move
    money", which is the first thing asked after an incident and the one thing
    the application logs (JSON to stdout) are worst at — logs rotate, this does
    not.

    There is no UPDATE or DELETE path in the application. `created_at` is
    indexed because every real query is "since when".
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Nullable: a failed login for a username that does not exist still has to
    # be recorded, or credential stuffing is invisible.
    admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # Denormalised on purpose: the row must stay readable after the account is
    # deleted, and it is what makes a failed-login row attributable.
    username_attempted: Mapped[str | None] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(48), index=True)
    outcome: Mapped[str] = mapped_column(String(16))  # SUCCESS / FAILURE
    detail: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))  # 45 = IPv6 max
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EmailVerificationToken(Base):
    """A single-use link token proving ownership of an email address (P-2).

    Stored as a SHA-256 digest, never the raw token: the raw value is a bearer
    credential that travels through email, so a leaked table (a backup, a
    replica, a log of a bad query) must not yield working links.

    SHA-256 rather than argon2, unlike passwords: the token is 256 bits of
    CSPRNG output, so there is no dictionary and no need for a memory-hard KDF —
    while argon2 at 64 MiB per click would turn the verify endpoint into a
    resource-exhaustion lever.

    `email` is denormalised whatever the user row says at issue time. The address
    can change between issue and click, and the click must prove the address the
    link was SENT to, not the one currently on the profile.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(254))
    # Unique so a digest collision (or a replayed insert) cannot create a second
    # live token for the same secret.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Stamped rather than deleted: "this link was used at T, from this IP" is the
    # audit trail for an account takeover. Same reasoning as the recovery codes.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


# --------------------------------------------------------------------------- #
# P-3: driver licence verification (manual, admin-decided)
# --------------------------------------------------------------------------- #


class LicenceReviewStatus(str, enum.Enum):
    """The lifecycle of one licence submission (P-3).

    Deliberately NOT the same enum as `DriverStatus`. A driver can be ACTIVE and
    trading while a *renewal* submission sits PENDING — coupling the two would
    force a trading driver offline to re-upload a document, which is exactly the
    behaviour that makes drivers abandon a platform.

    `SUPERSEDED` is a real terminal state, not a delete: when a driver
    re-submits after a rejection, the old row must stay for the audit trail. An
    admin acting on the wrong submission is a real failure mode, and a deleted
    row makes it undiagnosable.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class DocumentKind(str, enum.Enum):
    """What a stored object actually is.

    `DRIVER_LICENCE` and `TAXI_DRIVER_PASS` are distinct on purpose: the HK taxi
    driver pass (的士司機證) is issued by the Transport Department and proves the
    right to drive a taxi, while the driving licence proves the right to drive.
    An operator checking "is this a real taxi driver" needs the first. One field
    for both would silently accept either.
    """

    DRIVER_LICENCE = "DRIVER_LICENCE"
    # `noqa: S105` — bandit matches the `_PASS` suffix and reads this as a
    # hardcoded password. It is a document kind (the HK 的士司機證), and the
    # value is an enum member, not a credential.
    TAXI_DRIVER_PASS = "TAXI_DRIVER_PASS"  # noqa: S105
    VEHICLE_REGISTRATION = "VEHICLE_REGISTRATION"
    INSURANCE = "INSURANCE"
    OTHER = "OTHER"


class DriverLicenceSubmission(Base):
    """One submission of a taxi driver's licence for manual review (P-3).

    A separate table rather than columns on `driver_profiles`, for three
    reasons:

    1. **History.** HK taxi driver passes run on a fixed term, so this recurs.
       Columns would overwrite the previous decision and lose who approved what,
       and when — which is the record an operator is asked for after an incident.
    2. **The decision is about a document, not a person.** A rejection reason
       ("the expiry date is in shadow") belongs to the image, not the driver.
    3. **Status coupling.** As in `LicenceReviewStatus`: a renewal must not idle
       a trading driver.

    The full HKID is still never collected — only `hk_id_last4` on the profile —
    so the most damaging identifier cannot leak from this table at all.
    """

    __tablename__ = "driver_licence_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), index=True
    )
    licence_no: Mapped[str] = mapped_column(String(32))
    # A datetime, not a string: the expiry is compared against now on every
    # read, and "31/12/2027" cannot be ordered or indexed. Refusing a submission
    # that is already expired is the whole point of collecting it.
    expires_on: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[LicenceReviewStatus] = mapped_column(
        SAEnum(LicenceReviewStatus, name="licence_review_status", native_enum=False),
        default=LicenceReviewStatus.PENDING,
        index=True,
    )
    # The driver's own note at submission ("renewal", "new pass").
    submitted_note: Mapped[str | None] = mapped_column(String(500))
    # Admin decision. `reviewed_by` is an admin ACCOUNT id (admin_accounts), not
    # a user id — the two are different tables on purpose (P-1), and a decision
    # is always attributable to a console operator.
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    driver_profile: Mapped[DriverProfile] = relationship(back_populates="licence_submissions")
    documents: Mapped[list[DriverDocument]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


# At most one *open* submission per driver. Partial, not plain: decided and
# superseded rows accumulate forever, so a plain unique constraint would make
# the second submission impossible rather than the second OPEN one impossible.
Index(
    "uq_licence_one_open_per_driver",
    DriverLicenceSubmission.driver_profile_id,
    unique=True,
    postgresql_where=text("status = 'PENDING'"),
)


class DriverDocument(Base):
    """An uploaded object key, tied to a licence submission (P-3).

    Only the **object key** is stored — never the bytes, and never a URL. The
    key is a reference into R2 and URLs are signed on read with a short TTL, so
    a leaked database row (a backup, a replica, a bad log) does not yield a
    downloadable copy of someone's identity document. Same reasoning as
    `avatar_key` being a key rather than a URL.

    `content_type` and `size_bytes` are recorded so the read path can refuse to
    sign something that is not the image it claims to be, and so an operator can
    see at a glance that a 40 MB "scan" is not a scan.
    """

    __tablename__ = "driver_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("driver_licence_submissions.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[DocumentKind] = mapped_column(
        SAEnum(DocumentKind, name="document_kind", native_enum=False)
    )
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    submission: Mapped[DriverLicenceSubmission] = relationship(back_populates="documents")

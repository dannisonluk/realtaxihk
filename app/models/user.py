"""Core identity and order domain: users, drivers, orders, money, sessions.

Split out of the original single `app/models/__init__.py`. Everything here is
reachable as `from app.models import ...` — the package `__init__` re-exports
each name, so no call site needed to change when this file was created.

Bounded context: **who the user is** (registration identity, phone/email
verification, the driver profile and its deposit) and **what they do**
(orders, the append-only ledger, session tokens, refund requests). Fleets,
admin console identity and P-3 licence review deliberately live elsewhere;
the only cross-context edges are the FKs documented on each class below.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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
    select,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

if TYPE_CHECKING:
    # Checker-only, for the same reason as the mirror image in
    # `app/models/licence.py`.
    from app.models.licence import DriverLicenceSubmission

__all__ = [
    "AccountStatus",
    "Base",
    "DriverDeposit",
    "DriverProfile",
    "DriverStatus",
    "Gender",
    "LedgerEntry",
    "LedgerEntryType",
    "Order",
    "OrderStatus",
    "OtpCode",
    "RefreshToken",
    "RefundRequest",
    "RefundStatus",
    "User",
    "UserRole",
    "_utcnow",
    "_uuid",
]


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
    # A phone number is required at registration but is NOT required to be
    # proven there, so many accounts may *claim* a number and at most one may
    # *verify* it. This column was `unique=True, index=True`, and that plain
    # UNIQUE is a denial-of-registration: whoever types your number first owns
    # it, and you can no longer sign up at all — a cheap attack, since the
    # number is public. Uniqueness now applies only to verified rows; see
    # `__table_args__` at the bottom of this class. The column stays NOT NULL
    # because the claim itself is mandatory.
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[UserRole] = mapped_column(
        SAEnum(
            UserRole,
            name="ck_users_role",
            native_enum=False,
            create_constraint=True,
        ),
        default=UserRole.PASSENGER,
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
        SAEnum(Gender, name="ck_users_gender", native_enum=False, create_constraint=True)
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
        SAEnum(
            AccountStatus,
            name="ck_users_account_status",
            native_enum=False,
            create_constraint=True,
        ),
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

    # A restricted account handed to a store reviewer or an auditor, created by
    # `scripts/ops/create_reviewer_account.py`. **Non-NULL is the marker** —
    # there is deliberately no separate boolean, so "this account expires" and
    # "this account is a reviewer" cannot drift apart.
    #
    # The expiry lives in the database rather than in an ops runbook because a
    # forgotten revocation is the failure mode that matters, and a date the
    # guard reads on every request cannot be forgotten. `require_active_user`
    # refuses the account the moment this is in the past, which means the
    # revocation needs nobody to remember anything.
    reviewer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    driver_profile: Mapped[DriverProfile | None] = relationship(back_populates="user")

    __table_args__ = (
        # Uniqueness on the phone applies only to rows that PROVED the number.
        #
        # Registration requires a number but does not verify it, so several
        # accounts may hold `+85291234567` while exactly one may ever verify it.
        # The partial predicate is what makes that statement true: without it
        # this index would be the plain UNIQUE that was just removed.
        #
        # It is also what makes *binding* safe. Two accounts proving the same
        # number at the same moment produce an IntegrityError rather than two
        # verified owners, so the service-layer "already verified elsewhere"
        # check is a courtesy message and this index is the authority — the
        # same division of labour as `uq_refund_pending_per_driver` below.
        Index(
            "uq_users_phone_e164_verified",
            "phone_e164",
            unique=True,
            postgresql_where=text("phone_verified_at IS NOT NULL"),
        ),
    )


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
        SAEnum(
            DriverStatus,
            name="ck_driver_profiles_status",
            native_enum=False,
            create_constraint=True,
        ),
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
    # (ordering is applied at query time, not here). The target class lives in
    # `app.models.licence`; the reference is a string so the two modules can be
    # imported in either order.
    licence_submissions: Mapped[list[DriverLicenceSubmission]] = relationship(
        back_populates="driver_profile", cascade="all, delete-orphan"
    )
    # Current GPS position (updated every 3-5s while online).
    # `Mapped[object | None]` is forced by GeoAlchemy2 — see the longer note on
    # `Order.pickup_location` below. The `| None` is real (nullable column).
    current_location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Driver-declared in-car environment capabilities. JSONB because the set of
    # flags is expected to grow without schema churn; the API validates the
    # known keys. NULL means "has not declared anything" (no claim, no badge).
    in_car_environment_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    @staticmethod
    async def for_user(session: AsyncSession, user_id: uuid.UUID) -> DriverProfile | None:
        """The driver profile belonging to `user_id`, or `None` if not a driver.

        This exact query existed in six places — twice as a copy-pasted
        `_driver_profile_of` (in `app/api/orders.py` and `app/api/fleets.py`,
        identical apart from a function-local `import`), and four more times
        inlined (`app/api/drivers.py` `_get_profile`, `app/api/tracking.py`,
        `app/api/ws.py`, `app/services/licence_service.get_profile`).

        It lives on the model rather than in a service because it is a pure
        lookup with no policy: it does not check status, does not raise, does not
        filter to ACTIVE. Every caller decides what a missing or non-ACTIVE
        profile means, and those answers genuinely differ — 404 in `fleets.py`
        (to avoid confirming a fleet exists), 403 in `tracking.py`, a silent
        `None` in `ws.py`. Sharing the *query* is the fix for the duplication;
        sharing the *verdict* would have been the bug.
        """
        return (
            (await session.execute(select(DriverProfile).where(DriverProfile.user_id == user_id)))
            .scalars()
            .first()
        )


Index("ix_driver_profiles_location", DriverProfile.current_location, postgresql_using="gist")


class DriverDeposit(Base):
    __tablename__ = "driver_deposits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), unique=True
    )
    balance_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # available
    held_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # locked pending refund
    required_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=500)
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
        SAEnum(
            OrderStatus,
            name="ck_orders_status",
            native_enum=False,
            create_constraint=True,
        ),
        default=OrderStatus.CREATED,
        index=True,
    )
    # Requested route.
    #
    # `Mapped[object]` on the two geography columns is **forced, not lazy**:
    # GeoAlchemy2's `Geography` does not override `python_type`, and the base
    # implementation returns `object` (SQLAlchemy 2.1 changed it from raising
    # `NotImplementedError`). Writing `Mapped[str]` or `Mapped[Any]` would claim a
    # guarantee the column type does not make, so a type checker would be
    # verifying against a fiction — worse than no annotation. The reader gets the
    # real description from `app/services/order/trip_service.py`, which is the only
    # place that parses these (WKT via `_wkt`).
    #
    # Every *other* `Mapped[object]` in this module was the same habit applied to
    # a `Numeric` column, where `python_type` is genuinely `Decimal`; those are
    # `Mapped[Decimal]` now.
    pickup_location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    pickup_address: Mapped[str] = mapped_column(Text)
    dropoff_location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    dropoff_address: Mapped[str] = mapped_column(Text)
    distance_km: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    taxi_type: Mapped[str] = mapped_column(String(10))
    # Fare estimate snapshot (Cap. 374D disclaimer fields included in fare_json)
    fare_json: Mapped[dict] = mapped_column(JSONB)
    tariff_version: Mapped[str] = mapped_column(String(60))
    estimated_total_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    # Phase-1 ride requirements, frozen with the order. NULL means no special
    # requirements; the API validates shape and bounds before storing.
    requirements_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Passenger-facing preferred payment methods, set by the passenger at order
    # creation. This is a preference/request, not a guarantee.
    payment_preference_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The assigned driver's declared methods, copied onto the order at grab time
    # so a passenger sees what this driver actually accepts even if the driver
    # later edits their profile.
    driver_payment_methods_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Auto-detected at creation from a geofence against premium_destinations.
    premium_destination_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("premium_destinations.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # A frozen snapshot (id/code/name/avatar) so `order_out` never needs a join
    # and the map tag stays stable for the life of the order.
    premium_destination_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    destination_area: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Broadcast config
    broadcast_radius_km: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=3.0)
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
        SAEnum(
            LedgerEntryType,
            name="ck_ledger_entries_entry_type",
            native_enum=False,
            create_constraint=True,
        )
    )
    amount_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # signed: +credit / -debit
    balance_after_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
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
    amount_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[RefundStatus] = mapped_column(
        SAEnum(
            RefundStatus,
            name="ck_refund_requests_status",
            native_enum=False,
            create_constraint=True,
        ),
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

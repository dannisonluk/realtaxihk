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
    SmallInteger,
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
from app.models.prebooking import OrderKind, PrebookState

if TYPE_CHECKING:
    # Checker-only, for the same reason as the mirror image in
    # `app/models/licence.py`.
    from app.models.fixed_offer import FixedPriceOffer
    from app.models.licence import DriverLicenceSubmission
    from app.models.prebooking import DriverBookingPreference

__all__ = [
    "INTERRUPTION_REASONS_BY_PARTY",
    "SAFETY_INTERRUPTION_REASONS",
    "AccountStatus",
    "Base",
    "DriverDeposit",
    "DriverProfile",
    "DriverStatus",
    "Gender",
    "InterruptionReason",
    "LedgerEntry",
    "LedgerEntryType",
    "Order",
    "OrderKind",
    "OrderParty",
    "OrderStatus",
    "OtpCode",
    "PasswordResetToken",
    "PrebookState",
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
    """The order lifecycle (P4 widened this — see `docs/IN_TRIP_REDESIGN.md` §2).

    Before P4 the machine ended at `IN_TRIP -> COMPLETED`, a dead end that could
    not express a mid-trip destination change or an early end. Three states were
    added, and the two invariants the old machine guaranteed are restated in
    `app/services/order/state_machine.py`:

    * `PENDING_ARRIVAL_CONFIRM` — the driver pressed "arrived" and the GPS check
      passed, but the passenger has not yet confirmed. Arrival is a two-sided
      fact, so this is a state rather than a flag.
    * `DESTINATION_CHANGED` — **non-terminal**; the trip continues and falls
      back to `IN_TRIP`.
    * `INTERRUPTED` — **terminal**; the trip ended early. Distinct from
      `CANCELLED` (which means "never departed"), because settlement, insurance
      and the earnings reports treat the two differently.
    """

    CREATED = "CREATED"
    BROADCASTING = "BROADCASTING"
    ACCEPTED = "ACCEPTED"
    PENDING_ARRIVAL_CONFIRM = "PENDING_ARRIVAL_CONFIRM"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    IN_TRIP = "IN_TRIP"
    DESTINATION_CHANGED = "DESTINATION_CHANGED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OrderFareMode(str, enum.Enum):
    METER = "METER"
    FIXED = "FIXED"


class OrderParty(str, enum.Enum):
    """Which side of an order acted. Used for `interrupted_by_kind`.

    Uppercase members, matching `DisputePartyKind`, because
    `DisputeService.open_for_interruption` compares against `"PASSENGER"` /
    `"DRIVER"` to pick the counterparty — two spellings of the same idea is how
    the auto-opened dispute silently ends up blaming the platform.
    """

    PASSENGER = "PASSENGER"
    DRIVER = "DRIVER"


class InterruptionReason(str, enum.Enum):
    """Why a trip ended early. A closed set, not free text.

    This is the evidence an operator judges afterwards, so it has to be
    countable — "47 interruptions were `PASSENGER_SICK` this month" is a
    sentence free text cannot produce. `OTHER` still requires a note; the enum
    classifies, the note explains.

    Both parties draw from this one enum. The client shows only the options
    that make sense for the role, but the **server validates** the pair
    (`interrupted_by_kind` x reason) — filtering in the UI is courtesy,
    validating on the server is authorisation.
    """

    ACCIDENT = "ACCIDENT"  # crash / traffic accident
    CONFLICT = "CONFLICT"  # an argument with the other party
    PASSENGER_MISCONDUCT = "PASSENGER_MISCONDUCT"  # abusive behaviour
    PASSENGER_SICK = "PASSENGER_SICK"  # vomiting / taken ill
    DRIVER_MISCONDUCT = "DRIVER_MISCONDUCT"  # driver abusive or refusing to continue
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    UNSAFE_ROUTE = "UNSAFE_ROUTE"  # road / route unsafe
    FARE_DISPUTE = "FARE_DISPUTE"
    OTHER = "OTHER"  # a note is mandatory


# Reasons that raise the dispute to a safety case (1-hour SLA, flag for
# operator attention). Kept as a set beside the enum rather than a property, so
# "which reasons are safety" is one visible list a reviewer can disagree with.
SAFETY_INTERRUPTION_REASONS = frozenset(
    {
        InterruptionReason.ACCIDENT,
        InterruptionReason.CONFLICT,
        InterruptionReason.PASSENGER_MISCONDUCT,
        InterruptionReason.DRIVER_MISCONDUCT,
        InterruptionReason.UNSAFE_ROUTE,
    }
)

# Reasons only one party can honestly give, because the reason *names* that
# party. A passenger filing `PASSENGER_MISCONDUCT` is accusing themselves, and a
# driver filing `DRIVER_MISCONDUCT` does the same; `PASSENGER_SICK` is the
# driver's to report, because a passenger does not declare themselves ill in
# order to end a trip they are paying for.
#
# `docs/IN_TRIP_REDESIGN.md` §6.1 — 「前端過濾是禮貌，後端校驗是授權」. The client
# shows only its own role's menu (`InterruptionReason.forPassenger` /
# `forDriver`), but a hidden option is not a refused one: this enum is the
# evidence an operator judges from, so a pair the interrupting party cannot
# honestly give is refused by the server rather than trusted to the menu.
#
# Derived from the enum rather than listed by hand, so a member added later is
# allowed for **both** parties until someone deliberately excludes it. That is
# the safe direction: the alternative silently makes a new reason unfilable.
_PASSENGER_CANNOT_FILE = frozenset(
    {InterruptionReason.PASSENGER_MISCONDUCT, InterruptionReason.PASSENGER_SICK}
)
_DRIVER_CANNOT_FILE = frozenset({InterruptionReason.DRIVER_MISCONDUCT})

INTERRUPTION_REASONS_BY_PARTY: dict[OrderParty, frozenset[InterruptionReason]] = {
    OrderParty.PASSENGER: frozenset(set(InterruptionReason) - _PASSENGER_CANNOT_FILE),
    OrderParty.DRIVER: frozenset(set(InterruptionReason) - _DRIVER_CANNOT_FILE),
}


class LedgerEntryType(str, enum.Enum):
    """`native_enum=False`, so adding a member needs no DB type change — but it
    *does* widen the `ck_ledger_entries_entry_type` CHECK constraint, and that
    is a migration (`alembic/versions/042a7bc3e54c…` is the precedent). The
    longest member, `CANCELLATION_PENALTY`, is 20 chars, well inside the
    `SAEnum` default `VARCHAR(30)`.
    """

    DEPOSIT_TOPUP = "DEPOSIT_TOPUP"
    WEEKLY_FEE_DEDUCTION = "WEEKLY_FEE_DEDUCTION"
    PENALTY_DEDUCTION = "PENALTY_DEDUCTION"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    FIXED_RIDE_FEE = "FIXED_RIDE_FEE"
    # P4: the per-trip platform fee, charged to the driver's deposit at `/start`
    # (DECISION-1). The fare itself never passes through the platform (Cap. 374D
    # intermediary), so this is the platform's actual revenue from a trip.
    PLATFORM_TRIP_FEE = "PLATFORM_TRIP_FEE"
    # P4: a defaulting party's cancellation penalty. The base is the order's own
    # estimate snapshot, not a flat fee (DECISION-5).
    CANCELLATION_PENALTY = "CANCELLATION_PENALTY"
    # P4: an operator's dispute ruling that moves money — a refund of the trip
    # fee, or a charge against one party. Distinct from ADJUSTMENT so a dispute
    # outcome is separable in a driver's statement.
    DISPUTE_ADJUSTMENT = "DISPUTE_ADJUSTMENT"


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

    fixed_offers: Mapped[list[FixedPriceOffer]] = relationship(
        back_populates="driver_profile", cascade="all, delete-orphan"
    )
    booking_preference: Mapped[DriverBookingPreference | None] = relationship(
        "DriverBookingPreference",
        back_populates="driver_profile",
        cascade="all, delete-orphan",
    )

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
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="RESTRICT"), unique=True
    )
    balance_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # available
    held_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # locked pending refund
    required_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=500)
    is_fulfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    # DECISION-3: NULL means the driver is deliberately locked out of accepting
    # trips; a non-NULL timestamp is the operator's explicit release after a
    # negative balance has been topped up. New rows default to now, so an ACTIVE
    # driver who has never defaulted is not accidentally locked.
    acceptance_unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    driver_profile: Mapped[DriverProfile] = relationship(back_populates="deposit")


class Order(Base):
    __tablename__ = "orders"

    __table_args__ = (
        Index("ix_orders_scheduled_pickup_at", "scheduled_pickup_at"),
        Index("ix_orders_prebook_state", "prebook_state"),
        Index(
            "ix_orders_prebook_due",
            "prebook_visible_from",
            "prebook_state",
        ),
    )

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
    # FIXED vs METER. Defaults to METER for existing rows and backfilled by the
    # migration; the column is non-null after Phase 2.
    fare_mode: Mapped[OrderFareMode] = mapped_column(
        SAEnum(
            OrderFareMode,
            name="ck_orders_fare_mode",
            native_enum=False,
            create_constraint=True,
        ),
        default=OrderFareMode.METER,
        index=True,
    )
    order_kind: Mapped[OrderKind] = mapped_column(
        SAEnum(
            OrderKind,
            name="ck_orders_order_kind",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        default=OrderKind.ON_DEMAND,
        server_default=OrderKind.ON_DEMAND.value,
    )
    scheduled_pickup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prebook_visible_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prebook_state: Mapped[PrebookState | None] = mapped_column(
        SAEnum(
            PrebookState,
            name="ck_orders_prebook_state",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=True,
    )
    dropoff_landmark_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("landmarks.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
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
    dropoff_landmark = relationship("Landmark")
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
    pickup_area: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Fixed-fare (一口價) fields. Null on METER orders.
    fixed_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fixed_price_offers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    driver_price_hkd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    platform_fee_hkd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    passenger_price_hkd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Receipt request. The passenger may ask for a receipt at any point in the
    # order's life; the *document* is frozen into `receipt_snapshot_json` at the
    # moment of asking and never recomputed, so a later tariff change or ledger
    # adjustment cannot rewrite a receipt the passenger already holds (Cap. 374D:
    # the platform is an intermediary, so its record of the fare must be as
    # immutable as the fare snapshot itself).
    receipt_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    receipt_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Broadcast config
    broadcast_radius_km: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=3.0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    driver_arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    # --- P4 in-trip lifecycle (docs/IN_TRIP_REDESIGN.md §3.1) --------------- #
    # Arrival is proven in two steps, so it has two timestamps. `driver_arrived_at`
    # (above) stays "arrival is CONFIRMED" and is written only alongside
    # `arrival_confirmed_at`; `arrival_claimed_at` is the weaker "the driver says
    # so" and may be refused. Collapsing them would let an unconfirmed claim lock
    # the passenger's cancel right — the exact defect the two-step exists to stop.
    arrival_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Distance at claim time, kept as dispute evidence. The live check only needs
    # "within radius?"; this records *how* close, which is what an operator reads
    # when the two accounts disagree.
    arrival_gps_distance_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    arrival_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_pin_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # `IN_TRIP` start; the platform trip fee is charged against it.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_fee_charged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The original dropoff, copied once on the first destination change and never
    # overwritten, so "where did they originally ask to go" stays answerable.
    original_dropoff_address: Mapped[str | None] = mapped_column(Text)
    original_dropoff_location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    destination_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    destination_change_count: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0"
    )
    # How the trip ended early. `interruption_reason` is a closed set (evidence
    # for the operator); `interrupted_by_kind` is which side ended it.
    interruption_reason: Mapped[InterruptionReason | None] = mapped_column(
        SAEnum(
            InterruptionReason,
            name="ck_orders_interruption_reason",
            native_enum=False,
            create_constraint=True,
            length=32,
        )
    )
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_by_kind: Mapped[OrderParty | None] = mapped_column(
        SAEnum(
            OrderParty,
            name="ck_orders_interrupted_by_kind",
            native_enum=False,
            create_constraint=True,
            length=16,
        )
    )

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


class PasswordResetToken(Base):
    """A single-use, expiring link token for the "forgot password" flow.

    Deliberately the same shape as `EmailVerificationToken`, for the same
    reasons: SHA-256 rather than argon2 (the token is 256 bits of CSPRNG, so
    there is no dictionary and no need for a memory-hard KDF — while argon2 at
    64 MiB per click would turn the reset endpoint into a resource-exhaustion
    lever), stored as a digest so a leaked backup yields no working links, and
    consumed by stamping `consumed_at` rather than deleting, because "this link
    was used at T, from this IP" is the account-takeover trail.

    There is deliberately **no** `email` column (unlike the verification token):
    a reset is always against the account's *current* address, resolved at
    request time, so there is nothing to denormalise.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Unique, so a digest collision (or a replayed insert) cannot leave two live
    # tokens for one secret.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Kept for the audit trail; never shown to the user.
    requested_ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


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
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="RESTRICT"), index=True
    )
    amount_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # True when the driver claimed less than the whole balance (P2-2): the
    # payout is the requested amount and the driver returns to ACTIVE on
    # approval. A full refund (`False`) is terminal — approval terminates.
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
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

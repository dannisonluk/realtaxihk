"""SQLAlchemy 2.0 declarative models — hkfastdc.com core domain.

This is the **public import path**: every model, enum and helper is importable
as `from app.models import User, OrderStatus, ...` exactly as it was when this
was one flat module. The implementation is split by bounded context so a reader
can find a table without scrolling past 800 lines of unrelated schema:

- `_base.py`     — the single `Base` every model shares (private)
- `user.py`      — users, driver profiles, deposits, orders, ledger, sessions
- `fleet.py`     — fleets, rosters, weekly settlement runs
- `admin.py`     — admin console identity, TOTP, sessions, audit, email tokens
- `licence.py`   — P-3 licence submissions and their uploaded documents

Why the split is safe
---------------------
SQLAlchemy resolves `relationship()` targets lazily, by class registry lookup,
so a class may reference another that is defined in a *different* module. What
the registry does need is that **every** module contributing a mapped class has
been imported before `configure_mappers()` runs — otherwise `User.driver_profile`
or `Fleet.memberships` would resolve to nothing when first touched.

Importing this package is what guarantees that: the imports below are not
convenience re-exports, they are the step that registers all five modules. The
explicit `configure_mappers()` call then forces resolution *here*, at import
time, so a broken relationship fails immediately on `import app.models` rather
than later at the first query of whichever unrelated table happened to touch it.

Design notes (unchanged):
- Money columns: Numeric(10,2) — HKD has no sub-cent usage on meter fares; ledger
  keeps signed amounts with an append-only constraint.
- Geo columns: PostGIS geography(Point,4326) (srid 4326 matches Google Maps).
- Orders carry a snapshot of the fare estimate (tariff_version + totals) so
  historical orders stay auditable even after tariff changes.
"""

from __future__ import annotations

from sqlalchemy.orm import configure_mappers

from app.models._base import Base
from app.models.admin import (
    DEFAULT_ADMIN_ROLE,
    AdminAccount,
    AdminAuditLog,
    AdminRecoveryCode,
    AdminRefreshToken,
    AdminRole,
    EmailVerificationToken,
)
from app.models.dispute import (
    DisputeCategory,
    DisputeMessage,
    DisputePartyKind,
    DisputeResolution,
    DisputeSeverity,
    DisputeSource,
    DisputeStatus,
    OrderDispute,
)
from app.models.driver_notification import DriverNotification, DriverNotificationKind
from app.models.fixed_offer import FixedOfferStatus, FixedPriceOffer
from app.models.fleet import (
    Fleet,
    FleetMemberRole,
    FleetMembership,
    FleetMemberStatus,
    FleetSettlementRun,
    FleetStatus,
)
from app.models.licence import (
    DocumentKind,
    DriverDocument,
    DriverLicenceSubmission,
    LicenceReviewStatus,
)
from app.models.order_event import OrderEvent, OrderEventType
from app.models.premium import (
    DestinationStatus,
    DriverPaymentMethod,
    PaymentMethod,
    PremiumDestination,
)
from app.models.recurring import RecurringFrequency, RecurringRide, RecurringStatus
from app.models.user import (
    INTERRUPTION_REASONS_BY_PARTY,
    SAFETY_INTERRUPTION_REASONS,
    AccountStatus,
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    Gender,
    InterruptionReason,
    LedgerEntry,
    LedgerEntryType,
    Order,
    OrderFareMode,
    OrderParty,
    OrderStatus,
    OtpCode,
    PasswordResetToken,
    RefreshToken,
    RefundRequest,
    RefundStatus,
    User,
    UserRole,
)

# Resolve every relationship now. Import order above is deliberate: the target
# classes must exist in the registry before this line, and a missing target
# (e.g. a module dropped from the imports) raises here instead of at query time
# — see the module docstring.
configure_mappers()

__all__ = [
    "DEFAULT_ADMIN_ROLE",
    "INTERRUPTION_REASONS_BY_PARTY",
    "SAFETY_INTERRUPTION_REASONS",
    "AccountStatus",
    "AdminAccount",
    "AdminAuditLog",
    "AdminRecoveryCode",
    "AdminRefreshToken",
    "AdminRole",
    "Base",
    "DestinationStatus",
    "DisputeCategory",
    "DisputeMessage",
    "DisputePartyKind",
    "DisputeResolution",
    "DisputeSeverity",
    "DisputeSource",
    "DisputeStatus",
    "DocumentKind",
    "DriverDeposit",
    "DriverDocument",
    "DriverLicenceSubmission",
    "DriverNotification",
    "DriverNotificationKind",
    "DriverPaymentMethod",
    "DriverProfile",
    "DriverStatus",
    "EmailVerificationToken",
    "FixedOfferStatus",
    "FixedPriceOffer",
    "Fleet",
    "FleetMemberRole",
    "FleetMemberStatus",
    "FleetMembership",
    "FleetSettlementRun",
    "FleetStatus",
    "Gender",
    "InterruptionReason",
    "LedgerEntry",
    "LedgerEntryType",
    "LicenceReviewStatus",
    "Order",
    "OrderDispute",
    "OrderEvent",
    "OrderEventType",
    "OrderFareMode",
    "OrderParty",
    "OrderStatus",
    "OtpCode",
    "PasswordResetToken",
    "PaymentMethod",
    "PremiumDestination",
    "RecurringFrequency",
    "RecurringRide",
    "RecurringStatus",
    "RefreshToken",
    "RefundRequest",
    "RefundStatus",
    "User",
    "UserRole",
]

"""Response schemas for the realtaxihk API.

`from app.api.schemas import OrderOut, FareEstimateOut, ...`

Why this package exists
-----------------------
FastAPI's `response_model=` is a **filter**, not just documentation: it
validates the returned object and drops every key the model does not declare.
Before this package, 29 request models lived inline in `app/api/*.py` and only
one route declared a response model, so `/openapi.json` published `{}` for
almost every response — which is why the real contract had to be pinned by
`mobile/tool/verify_contract.dart` and captured fixtures.

The models here are derived **from those captured responses**, not from reading
the handlers, and the fixture set is the acceptance test: adding a
`response_model=` that omits a field a fixture contains will fail the contract
check.

Coverage: **all operations now declare a real `response_model=`**.
`scripts/verify/audit_response_models.py` proves that no fixture key is dropped
by its model — run it after touching anything here. It is deliberately separate
from `mobile/tool/verify_contract.dart`: that one validates the *Dart decoders*
against the bytes, this one validates the *schemas* against the fixtures, and a
rename that updates both the model and the handler passes every Python test
while breaking the client.

Conventions (see each module for the detail):
- Money and meter figures are `str` on the wire. `money_str` = 2 dp for stored
  balances, `meter_str` = 1 dp for fare figures; the fixtures pin which.
- Optional fields are `X | None = None`, never bare `X | None` with a default
  mismatch, so an absent key and a null key are handled the same way.
- No field is declared that no fixture (or, for the fixture-less routes, no
  serializer) actually produces.
- A route whose handler emits a key only sometimes needs
  `response_model_exclude_unset=True`.

Deliberately **not** here: request-body models. Request schemas live in their
API modules (or dedicated schema modules) and are not exported from this
package, so the audit script can prove every exported schema is reachable as a
response model.
"""

from __future__ import annotations

from app.api.schemas._envelope import (
    ErrorEnvelope,
    ListEnvelope,
    OkLogoutOut,
    OkOut,
    OkRevokedOut,
    PageEnvelope,
    PasswordForgotOut,
)
from app.api.schemas.admin import (
    AdminAccountCreatedOut,
    AdminAccountOut,
    AdminAccountPageOut,
    AdminActiveChangeOut,
    AdminDepositDetailOut,
    AdminDepositOut,
    AdminDisputeDetailOut,
    AdminDisputePageOut,
    AdminDisputeRowOut,
    AdminLedgerRowOut,
    AdminLiveDriverOut,
    AdminLiveDriversOut,
    AdminOrderDetailOut,
    AdminOrderPageOut,
    AdminOrderRowOut,
    AdminPasswordResetOut,
    AdminRoleChangeOut,
    AdminSearchOut,
    AdminSearchResultOut,
    AuditPageOut,
    AuditRowOut,
    DepositAdjustOut,
    DepositGrantOut,
    DepositUnlockOut,
    DisputeMessageOut,
    DisputeResolveOut,
    DisputeStatsOut,
    DriverDetailOut,
    DriverFleetBlockOut,
    DriverPageOut,
    DriverReviewOut,
    DriverRowOut,
    RefundPageOut,
    SettlementPreviewOut,
)
from app.api.schemas.admin_auth import (
    AdminLoginOut,
    AdminSessionAccountOut,
    AdminSessionOut,
    ChallengeOut,
    EnrolmentOut,
    TotpEnrolOut,
)
from app.api.schemas.admin_licence import (
    LicenceDecisionOut,
    LicenceQueueOut,
    LicenceQueueRowOut,
    LicenceReviewDetailOut,
    ReviewDocumentOut,
)
from app.api.schemas.analytics import (
    AdminAnalyticsCancellationsOut,
    AdminAnalyticsLatencyOut,
    AdminAnalyticsOperationsFunnelOut,
    AdminAnalyticsOperationsOut,
    AdminAnalyticsOperationsRangeOut,
    AdminAnalyticsSupplyOut,
)
from app.api.schemas.driver import (
    DepositOut,
    DriverProfileOut,
    DriverProfileWithDepositOut,
    RefundDecisionOut,
    RefundOut,
    RefundRequestOut,
    RefundViewOut,
)
from app.api.schemas.driver_attributes import (
    DriverEnvironmentOut,
    DriverPaymentMethodsOut,
)
from app.api.schemas.driver_notification import (
    DriverNotificationOut,
    DriverNotificationPageOut,
    DriverNotificationReadOut,
    DriverNotificationsReadAllOut,
)
from app.api.schemas.fare import (
    FareEstimateOut,
    FareSnapshotOut,
    FareSurchargeOut,
)
from app.api.schemas.fixed_offer import FixedOfferListOut, FixedOfferOut
from app.api.schemas.fleet import (
    FleetMemberListOut,
    FleetMemberRowOut,
    FleetMembershipOut,
    FleetOut,
    FleetPageOut,
    FleetSettlementListOut,
    FleetSettlementRowOut,
    FleetSettlementRunOut,
    FleetViewOut,
    SettlementRunOut,
)
from app.api.schemas.identity import (
    AdminMeOut,
    AuthMeOut,
    AvatarPresignOut,
    EmailConfirmOut,
    EmailRequestOut,
    OtpRequestOut,
    PhoneBindOut,
    PhoneReverifyOut,
    ProfileOut,
    TokenPairOut,
    UsernameCheckOut,
    UserOut,
)
from app.api.schemas.licence import (
    LicenceDocumentOut,
    LicenceListOut,
    LicenceSubmissionOut,
    PresignedUploadOut,
)
from app.api.schemas.order import (
    LedgerEntryOut,
    LedgerPageOut,
    OrderOut,
    OrderPageOut,
    PassengerDisputeOut,
    TripLocationOut,
)
from app.api.schemas.prebooking import (
    DriverBookingPreferenceIn,
    DriverBookingPreferenceOut,
    LandmarkListOut,
    LandmarkOut,
)
from app.api.schemas.premium import (
    PremiumDestinationListOut,
    PremiumDestinationOut,
)
from app.api.schemas.receipt import ReceiptOut
from app.api.schemas.recurring import RecurringRideListOut, RecurringRideOut
from app.api.schemas.service_area import ServiceAreaBounds, ServiceAreaCheckResult

__all__ = [
    "AdminAccountCreatedOut",
    "AdminAccountOut",
    "AdminAccountPageOut",
    "AdminActiveChangeOut",
    "AdminAnalyticsCancellationsOut",
    "AdminAnalyticsLatencyOut",
    "AdminAnalyticsOperationsFunnelOut",
    "AdminAnalyticsOperationsOut",
    "AdminAnalyticsOperationsRangeOut",
    "AdminAnalyticsSupplyOut",
    "AdminDepositDetailOut",
    "AdminDepositOut",
    "AdminDisputeDetailOut",
    "AdminDisputePageOut",
    "AdminDisputeRowOut",
    "AdminLedgerRowOut",
    "AdminLiveDriverOut",
    "AdminLiveDriversOut",
    "AdminLoginOut",
    "AdminMeOut",
    "AdminOrderDetailOut",
    "AdminOrderPageOut",
    "AdminOrderRowOut",
    "AdminPasswordResetOut",
    "AdminRoleChangeOut",
    "AdminSearchOut",
    "AdminSearchResultOut",
    "AdminSessionAccountOut",
    "AdminSessionOut",
    "AuditPageOut",
    "AuditRowOut",
    "AuthMeOut",
    "AvatarPresignOut",
    "ChallengeOut",
    "DepositAdjustOut",
    "DepositGrantOut",
    "DepositOut",
    "DepositUnlockOut",
    "DisputeMessageOut",
    "DisputeResolveOut",
    "DisputeStatsOut",
    "DriverBookingPreferenceIn",
    "DriverBookingPreferenceOut",
    "DriverDetailOut",
    "DriverEnvironmentOut",
    "DriverFleetBlockOut",
    "DriverNotificationOut",
    "DriverNotificationPageOut",
    "DriverNotificationReadOut",
    "DriverNotificationsReadAllOut",
    "DriverPageOut",
    "DriverPaymentMethodsOut",
    "DriverProfileOut",
    "DriverProfileWithDepositOut",
    "DriverReviewOut",
    "DriverRowOut",
    "EmailConfirmOut",
    "EmailRequestOut",
    "EnrolmentOut",
    "ErrorEnvelope",
    "FareEstimateOut",
    "FareSnapshotOut",
    "FareSurchargeOut",
    "FixedOfferListOut",
    "FixedOfferOut",
    "FleetMemberListOut",
    "FleetMemberRowOut",
    "FleetMembershipOut",
    "FleetOut",
    "FleetPageOut",
    "FleetSettlementListOut",
    "FleetSettlementRowOut",
    "FleetSettlementRunOut",
    "FleetViewOut",
    "LandmarkListOut",
    "LandmarkOut",
    "LedgerEntryOut",
    "LedgerPageOut",
    "LicenceDecisionOut",
    "LicenceDocumentOut",
    "LicenceListOut",
    "LicenceQueueOut",
    "LicenceQueueRowOut",
    "LicenceReviewDetailOut",
    "LicenceSubmissionOut",
    "ListEnvelope",
    "OkLogoutOut",
    "OkOut",
    "OkRevokedOut",
    "OrderOut",
    "OrderPageOut",
    "OtpRequestOut",
    "PageEnvelope",
    "PassengerDisputeOut",
    "PasswordForgotOut",
    "PhoneBindOut",
    "PhoneReverifyOut",
    "PremiumDestinationListOut",
    "PremiumDestinationOut",
    "PresignedUploadOut",
    "ProfileOut",
    "ReceiptOut",
    "RecurringRideListOut",
    "RecurringRideOut",
    "RefundDecisionOut",
    "RefundOut",
    "RefundPageOut",
    "RefundRequestOut",
    "RefundViewOut",
    "ReviewDocumentOut",
    "ServiceAreaBounds",
    "ServiceAreaCheckResult",
    "SettlementPreviewOut",
    "SettlementRunOut",
    "TokenPairOut",
    "TotpEnrolOut",
    "TripLocationOut",
    "UserOut",
    "UsernameCheckOut",
]

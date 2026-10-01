"""Response schemas for the realtaxihk API.

`from app.api.schemas import OrderOut, FareEstimateOut, ...`

Why this package exists
-----------------------
FastAPI's `response_model=` is a **filter**, not just documentation: it
validates the returned object and drops every key the model does not declare.
Before this package, 29 request models lived inline in `app/api/*.py` and only
one route declared a response model, so `/openapi.json` published `{}` for
almost every response — which is why the real contract had to be pinned by
`mobile/tool/verify_contract.dart` and 54 captured fixtures.

The models here are derived **from those captured responses**, not from reading
the handlers, and the fixture set is the acceptance test: adding a
`response_model=` that omits a field a fixture contains will fail the contract
check. See `mobile/test/fixtures/manifest.json` for the route → fixture map.

Coverage: **all 69 operations now declare a real `response_model=`** (it was 1
of 69 before this package). `scripts/audit_response_models.py` proves that no
fixture key is dropped by its model — run it after touching anything here. It
is deliberately separate from `mobile/tool/verify_contract.dart`: that one
validates the *Dart decoders* against the bytes, this one validates the
*schemas* against the fixtures, and a rename that updates both the model and the
handler passes every Python test while breaking the client.

Conventions (see each module for the detail):
- Money and meter figures are `str` on the wire. `money_str` = 2 dp for stored
  balances, `meter_str` = 1 dp for fare figures; the fixtures pin which.
- Optional fields are `X | None = None`, never bare `X | None` with a default
  mismatch, so an absent key and a null key are handled the same way.
- No field is declared that no fixture (or, for the fixture-less routes, no
  serializer) actually produces.
- **A route whose handler emits a key only sometimes needs
  `response_model_exclude_unset=True`.** `GET /drivers/me` is the one case: the
  no-deposit path omits `balance_hkd`/`held_hkd` entirely, and a plain
  `response_model=` would materialise them from their defaults and publish a
  balance the driver does not have.

Deliberately **not** here: the two distinct fare shapes are in `fare.py` rather
than merged, and the three list envelopes are in `_envelope.py` rather than
unified, because those differences are load-bearing on the client.
"""

from __future__ import annotations

from app.api.schemas._envelope import (
    ErrorEnvelope,
    ListEnvelope,
    OkLogoutOut,
    OkOut,
    OkRevokedOut,
    PageEnvelope,
)
from app.api.schemas.admin import (
    AdminAccountCreatedOut,
    AdminAccountOut,
    AdminAccountPageOut,
    AdminDepositDetailOut,
    AdminDepositOut,
    AdminLedgerRowOut,
    AdminPasswordResetOut,
    AdminRoleChangeOut,
    AuditPageOut,
    AuditRowOut,
    DepositAdjustOut,
    DepositGrantOut,
    DriverDetailOut,
    DriverFleetBlockOut,
    DriverPageOut,
    DriverReviewOut,
    DriverRowOut,
    RefundPageOut,
)
from app.api.schemas.admin_auth import (
    AdminLoginOut,
    AdminSessionAccountOut,
    AdminSessionOut,
    ChallengeOut,
    EnrolmentOut,
    TotpEnrolOut,
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
from app.api.schemas.fare import (
    FareEstimateOut,
    FareSnapshotOut,
    FareSurchargeOut,
)
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
    EmailConfirmOut,
    EmailRequestOut,
    OtpRequestOut,
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
from app.api.schemas.licence_admin import (
    LicenceDecisionOut,
    LicenceQueueOut,
    LicenceQueueRowOut,
    LicenceReviewDetailOut,
    ReviewDocumentOut,
)
from app.api.schemas.order import (
    LedgerEntryOut,
    LedgerPageOut,
    OrderOut,
    OrderPageOut,
    TripLocationOut,
)

__all__ = [
    "AdminAccountCreatedOut",
    "AdminAccountOut",
    "AdminAccountPageOut",
    "AdminDepositDetailOut",
    "AdminDepositOut",
    "AdminLedgerRowOut",
    "AdminLoginOut",
    "AdminMeOut",
    "AdminPasswordResetOut",
    "AdminRoleChangeOut",
    "AdminSessionAccountOut",
    "AdminSessionOut",
    "AuditPageOut",
    "AuditRowOut",
    "AuthMeOut",
    "ChallengeOut",
    "DepositAdjustOut",
    "DepositGrantOut",
    "DepositOut",
    "DriverDetailOut",
    "DriverFleetBlockOut",
    "DriverPageOut",
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
    "FleetMemberListOut",
    "FleetMemberRowOut",
    "FleetMembershipOut",
    "FleetOut",
    "FleetPageOut",
    "FleetSettlementListOut",
    "FleetSettlementRowOut",
    "FleetSettlementRunOut",
    "FleetViewOut",
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
    "PhoneReverifyOut",
    "PresignedUploadOut",
    "ProfileOut",
    "RefundDecisionOut",
    "RefundOut",
    "RefundPageOut",
    "RefundRequestOut",
    "RefundViewOut",
    "ReviewDocumentOut",
    "SettlementRunOut",
    "TokenPairOut",
    "TotpEnrolOut",
    "TripLocationOut",
    "UserOut",
    "UsernameCheckOut",
]

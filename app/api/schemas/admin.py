"""Admin console schemas — driver rows, deposit writes, review, admin identity.

These are console-facing, so they carry more than the driver-facing views: an
operator needs the review state and the money at once, in one request.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.api.schemas.driver import RefundOut

__all__ = [
    "AdminAccountOut",
    "AdminDepositDetailOut",
    "AdminLedgerRowOut",
    "AuditPageOut",
    "AuditRowOut",
    "DepositAdjustOut",
    "DepositGrantOut",
    "DriverDetailOut",
    "DriverFleetBlockOut",
    "DriverPageOut",
    "DriverReviewOut",
    "DriverRowOut",
    "RefundPageOut",
]


class AdminDepositOut(BaseModel):
    """The deposit as the console sees it — the same three balances the driver
    sees, plus nothing. Kept separate from `DepositOut` so an admin-only field
    can be added later without widening the driver's contract."""

    balance_hkd: str
    held_hkd: str
    required_hkd: str
    is_fulfilled: bool


class AdminDepositDetailOut(AdminDepositOut):
    """The deposit on the driver **detail** page.

    Adds two derived fields the roster does not need. `has_account` is the
    difference between "never credited" and "credited, now at zero": the route
    synthesises a deposit block for the former so the progress bar has a target,
    and without this flag the page cannot tell the two apart. `shortfall_hkd`
    is `required - balance`, floored at zero, precomputed because it is
    expensive to recompute in the client and the rounding must match the
    server's.

    Both are emitted on the no-row path too — see `_deposit_detail_out`.
    """

    has_account: bool
    shortfall_hkd: str


class AdminLedgerRowOut(BaseModel):
    """One ledger row on the admin detail page.

    Carries `reference` and `created_by`, which the driver-facing
    `/drivers/me/ledger` deliberately omits: an operator's user id is internal,
    but for an `ADJUSTMENT` — a discretionary move with no upstream event —
    attribution is the whole reason the record is kept.
    """

    id: int
    entry_type: str
    amount_hkd: str
    balance_after_hkd: str
    order_id: str | None
    note: str | None
    reference: str | None
    created_by: str | None
    created_at: str | None


class DriverFleetBlockOut(BaseModel):
    """The driver's active fleet membership, on the detail page.

    All-nullable because the block itself is nullable: a driver in no fleet has
    `fleet: null`, and this model describes the populated case. `member_role` is
    the driver's role *within* the fleet (member/owner), distinct from the
    platform-level `role` on a user.
    """

    fleet_id: str
    name: str
    license_no: str | None
    status: str
    weekly_fee_discount_percent: str
    member_role: str
    joined_at: str | None


class DriverRowOut(BaseModel):
    """One row of `GET /admin/drivers` — the roster view.

    Deliberately **narrower** than `DriverProfileOut`: it carries no `user_id`,
    because the console lists drivers by their taxi identifiers and an account
    id is not useful to an operator. `taxi_driver_plate_no` is the HK 的士司機證
    number, which is what an operator actually cross-checks.
    """

    id: str
    status: str
    taxi_type: str
    taxi_driver_plate_no: str
    vehicle_reg_mark: str


class DriverDetailOut(DriverRowOut):
    """One driver, expanded — what the console shows on a detail pane.

    Same fields as the row plus the identity documents, the KYC review state,
    the deposit, the statement, the refund history, and the active fleet. A
    superset of `DriverRowOut` rather than a parallel model, so the two cannot
    disagree about the five fields they share.

    `ledger` and `refunds` are **capped, newest-first** — the statement grows
    without bound and an operator inspecting a driver wants the recent position,
    not the archive. Both are wrapped in `{"items": [...]}` rather than returned
    bare so a sibling key (a total, a cursor) can be added without breaking
    clients; note they are *not* `ListEnvelope`, since that carries no ordering
    guarantee and this contract does.
    """

    user_id: str
    hk_id_last4: str
    is_online: bool
    kyc_reviewed_at: str | None
    created_at: str | None
    updated_at: str | None
    deposit: AdminDepositDetailOut
    ledger: dict[str, list[AdminLedgerRowOut]]
    refunds: dict[str, list[RefundOut]]
    fleet: DriverFleetBlockOut | None = None


class DriverPageOut(BaseModel):
    """`GET /admin/drivers` — offset-paginated driver roster.

    Typed rather than the generic `PageEnvelope`: the generic one declares
    `items: list[Any]`, which publishes `items: {}` and leaves the *only* part
    of the response a client renders undocumented. The console's roster page
    reads these fields directly, so the element shape belongs in the contract.

    `total` counts all drivers matching the filter, not just this page — the
    console renders "N drivers" from it.
    """

    items: list[DriverRowOut]
    total: int
    limit: int
    offset: int


class RefundPageOut(BaseModel):
    """`GET /admin/refunds` — offset-paginated refund queue.

    Same reasoning as `DriverPageOut`. Newest-first by default so PENDING rows
    surface; `total` respects the status filter.
    """

    items: list[RefundOut]
    total: int
    limit: int
    offset: int


class DepositGrantOut(BaseModel):
    """`POST /admin/drivers/{id}/deposit/grant` — the result of a manual credit.

    `reference` is returned because it is the **idempotency key** of the ledger
    write (`uq_ledger_reference`): a retried grant with the same reference
    cannot double-post. Echoing it lets an operator confirm which write landed.
    `driver_status` is included because a grant can flip a driver from
    DEPOSIT_REQUIRED to ACTIVE, and the operator needs to see that happened
    without a follow-up request.
    """

    id: str
    driver_status: str
    balance_hkd: str
    is_fulfilled: bool
    reference: str


class DriverReviewOut(BaseModel):
    """`POST /admin/drivers/{id}/review` — KYC decision acknowledgement.

    **Only `{id, status}`**, not the profile the list endpoint returns. Called
    out in `verify_contract.dart` as something reading the source did not make
    obvious: a client that reused its row model here would find two fields
    missing. The narrow shape is intentional — the decision is a state change,
    and the list is where the row is read from.
    """

    id: str
    status: str


class DepositAdjustOut(DepositGrantOut):
    """`POST /admin/drivers/{id}/deposit/adjust` — the result of a correction.

    Identical to `DepositGrantOut` plus `amount_hkd`, the signed delta that was
    posted — a grant's amount is always the credit requested, but an adjustment
    is signed (positive credits, negative debits), and the operator needs to see
    which direction landed. Declared as its own name so the two routes can
    diverge without silently changing the grant contract.
    """

    amount_hkd: str


class AdminAccountOut(BaseModel):
    """An admin account row, as the console reads it.

    Returned by `GET /api/v1/admin/accounts`. Distinct from the other two admin
    identity shapes: `GET /api/v1/auth/me` returns the four-field `AdminMeOut`
    (`app/api/schemas/identity.py`) carrying `email_masked`, and session
    responses carry `AdminSessionAccountOut` (`app/api/schemas/admin_auth.py`).
    Three shapes, three audiences — account administration needs the metadata
    neither of the other two carries.

    `admin_role` is the RBAC rank. It is present so the accounts page can show
    and edit it; it is not an authority claim, and `require_role` still reads
    the live row on every request.

    `totp_enrolled` is derived, not the secret: the secret never leaves the
    server, and enrolment is the only thing a client acts on.
    """

    id: str
    username: str
    email: str
    full_name: str | None
    admin_role: str
    is_active: bool
    totp_enrolled: bool
    last_login_at: str | None
    created_at: str


class AuditRowOut(BaseModel):
    """One `admin_audit_log` row, as the console's audit page reads it.

    `payload` is the structured before/after and is `dict | None` rather than a
    typed model, deliberately: the shape differs per event, and pinning it to
    one event's fields would make every other event render as an empty object.
    The console renders it as key/value pairs. It is additive — rows written
    before the column existed, and the login events, carry `None`.

    `actor_username` is the *attempted* username, denormalised on the row. It
    is present even when `actor_id` is null, which is the case for a failed
    login against a username that does not exist — the row must stay readable
    and attributable after the account it names is gone.
    """

    id: str
    actor_id: str | None
    actor_username: str | None
    event: str
    outcome: str
    detail: str | None
    payload: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: str


class AuditPageOut(BaseModel):
    """`GET /admin/audit` — offset-paginated audit trail, newest first.

    Same reasoning as `DriverPageOut`: typed rather than the generic
    `PageEnvelope`, so the element shape is documented instead of `items: {}`.
    """

    items: list[AuditRowOut]
    total: int
    limit: int
    offset: int

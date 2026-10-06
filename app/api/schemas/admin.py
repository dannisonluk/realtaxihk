"""Admin console schemas — driver rows, deposit writes, review, admin identity.

These are console-facing, so they carry more than the driver-facing views: an
operator needs the review state and the money at once, in one request.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.api.schemas.driver import RefundOut

__all__ = [
    "AdminAccountCreatedOut",
    "AdminAccountOut",
    "AdminAccountPageOut",
    "AdminDepositDetailOut",
    "AdminDisputeDetailOut",
    "AdminDisputePageOut",
    "AdminDisputeRowOut",
    "AdminLedgerRowOut",
    "AdminOrderDetailOut",
    "AdminOrderPageOut",
    "AdminOrderRowOut",
    "AdminPasswordResetOut",
    "AdminRoleChangeOut",
    "AuditPageOut",
    "AuditRowOut",
    "DepositAdjustOut",
    "DepositGrantOut",
    "DisputeMessageOut",
    "DisputeResolveOut",
    "DisputeStatsOut",
    "DriverDetailOut",
    "DriverFleetBlockOut",
    "DriverPageOut",
    "DriverReviewOut",
    "DriverRowOut",
    "RefundPageOut",
    "SettlementPreviewOut",
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


class AdminAccountPageOut(BaseModel):
    """`GET /admin/accounts` — the admin roster.

    Every field of `AdminAccountOut` plus a derived `super_admin_count`.

    The count is on the *page* rather than on each row because it is a property
    of the whole set, and the console needs it to answer one question before it
    renders the demote button: "is this the last one?" Without it the UI either
    offers an action the server will refuse, or re-derives the rule client-side
    and gets it wrong the first time the rule changes.

    It counts **active** SUPER_ADMIN rows, matching
    `AdminAccountService.count_super_admins` — a deactivated super admin is not
    a way to grant roles, so counting them would let the last usable one be
    demoted while a disabled name kept the check satisfied.
    """

    items: list[AdminAccountOut]
    super_admin_count: int
    total: int


class AdminAccountCreatedOut(AdminAccountOut):
    """`POST /admin/accounts` — the new account, plus one onboarding fact.

    Adds `totp_enrolment_pending`, always `true` on this path. It is stated
    rather than implied because it is the difference between "created" and
    "usable": the account has no TOTP secret until first login, and an operator
    who reads `201 Created` as "they can log in now" will hand over credentials
    that do not work yet.
    """

    totp_enrolment_pending: bool


class AdminRoleChangeOut(BaseModel):
    """`PATCH /admin/accounts/{id}/role` — acknowledgement of a role change.

    Carries **both** `previous_role` and `admin_role`. The row alone only holds
    the current value, and "what was it before" is the thing anyone reading the
    response is trying to confirm. Echoing it here also means the console need
    not re-fetch the roster to show the transition it just caused.

    Mirrors the `AdminRoleChange` audit payload's `from`/`to`, so the response
    and the audit row cannot disagree about what happened.
    """

    id: str
    previous_role: str
    admin_role: str
    super_admin_count: int


class AdminPasswordResetOut(BaseModel):
    """`POST /admin/accounts/{id}/password/reset` — acknowledgement only.

    No password and no hash in the body. The new password is supplied *by* the
    caller and is never returned — a reset response is not a place to hand a
    credential back, because it would then sit in a response log.

    `sessions_revoked` is reported so the operator knows the reset also ejected
    anyone already signed in. It is informational; there is no partial-success
    form of this operation.
    """

    id: str
    sessions_revoked: bool


class AdminActiveChangeOut(BaseModel):
    """`PATCH /admin/accounts/{id}/active` — acknowledgement of a state change.

    `previous_is_active` is echoed for the same reason `AdminRoleChangeOut`
    echoes `previous_role`: the row holds only the new value, and "what was it
    before" is what a reader of the response is confirming. It also lets the
    console show the transition it just caused without re-fetching the roster.

    `sessions_revoked` distinguishes the two directions. Deactivating ejects the
    account's live sessions in the same transaction — that is most of the point,
    since `require_admin` re-reads `is_active` on every request but the refresh
    family would otherwise keep rotating for its full lifetime. Reactivating
    revokes nothing, because there is nothing to revoke: a deactivated account
    cannot authenticate, so it holds no session to kill.
    """

    id: str
    is_active: bool
    previous_is_active: bool
    sessions_revoked: bool


class AdminOrderRowOut(BaseModel):
    """One row of `GET /admin/orders` — the orders table.

    Deliberately **not** `OrderOut`. That model is the passenger and driver
    contract and is intentionally identity-free: a driver must not be handed a
    passenger's account id. An operator looking at "my passenger says the
    driver never showed" needs exactly the opposite — who and where.

    So this carries the identifiers the passenger view omits, and it carries
    them as **ids, not names or phones**. Resolving an id to a human is a
    separate, audited action; a list endpoint that inlines phone numbers turns
    every scroll of the orders page into a bulk PII read.

    The four timestamps are the whole point of the page. `accepted_at`,
    `driver_arrived_at`, `completed_at` and `cancelled_at` answer the question
    the console could not answer before: "where is this trip now, and how long
    has it been there". They are nullable individually because a trip that
    never got a driver has no `accepted_at` — all-null with a `CREATED` status
    is meaningful, not missing data.

    `pickup_address` / `dropoff_address` are the requested text, not a
    geocoded value: they are what the passenger typed and what the driver was
    shown, so they are the right thing to quote back in a dispute.

    `fare_mode` is on the row rather than the detail page because it changes
    what the fare *means*. A `FIXED` trip is one the passenger agreed to at a
    quoted price, so "the meter says more than that" is not a discrepancy to
    investigate — it is what a fixed fare is. A `METER` trip has no such
    agreement. An operator scanning the table for a billing complaint needs to
    tell those apart without opening fifty rows.
    """

    id: str
    status: str
    taxi_type: str
    fare_mode: str
    passenger_id: str
    driver_id: str | None
    pickup_address: str
    dropoff_address: str
    distance_km: str
    estimated_total_hkd: str
    discount_percent: str
    accepted_at: str | None
    driver_arrived_at: str | None
    completed_at: str | None
    cancelled_at: str | None
    cancellation_reason: str | None
    created_at: str | None


class AdminOrderPageOut(BaseModel):
    """`GET /admin/orders` — offset-paginated order table, newest first.

    Typed rather than the generic `PageEnvelope`, same reasoning as
    `DriverPageOut`. `total` respects the filters, so the operator sees the size
    of the queue they filtered to rather than of the archive.
    """

    items: list[AdminOrderRowOut]
    total: int
    limit: int
    offset: int


class AdminOrderDetailOut(AdminOrderRowOut):
    """`GET /admin/orders/{id}` — the full picture for one trip.

    Everything the row carries, plus:

    * `fare` — the **frozen snapshot** (`orders.fare_json`), not a recomputed
      estimate. A fare recomputed today is a different number after any tariff
      change, and the disputed amount is the one the passenger was quoted.
    * `tariff_version` — which tariff produced that snapshot, so a historical
      order stays explicable.
    * `timeline` — the same timestamps as an ordered list with an explicit
      `elapsed_seconds` per step, computed server-side so the console does not
      have to subtract two local-time strings and get the DST case wrong.
    * `ledger` — the ledger entries referencing this order. Usually empty (a
      fare is not a ledger entry; only a penalty or a manual adjustment is),
      which is itself worth seeing.
    * `broadcast` — the radius the trip was offered within, so "why did nobody
      take it" is answerable.

    It also carries the **frozen record of what the passenger asked for**
    (`requirements`, `payment_preference`, `premium_destination`, the two area
    codes, and the receipt request). A dispute is very often about exactly
    this — "the driver had the radio on the whole way and I asked for a silent
    car", "I asked to pay by Octopus and he would only take cash", "I asked for
    a receipt and never got one" — and the operator's job is to compare the
    request *as recorded at booking time* against what happened, not to
    arbitrate between two recollections of it.

    These are omitted from `AdminOrderRowOut` on purpose. The list is a
    fixed-column table, so pulling a requirements/receipt JSONB blob per row
    for every scroll is a cost with no reader.

    `requirements` is `None` when the passenger asked for nothing special.
    That is not the same as `{}`: the write path stores `None` rather than an
    object of all-false flags precisely so "never mentioned a pet" stays
    distinguishable from "said no pet".
    """

    tariff_version: str
    fare: dict[str, Any]
    broadcast_radius_km: str
    timeline: list[dict[str, Any]]
    ledger: dict[str, list[AdminLedgerRowOut]]
    requirements: dict[str, Any] | None
    payment_preference: list[str]
    driver_payment_methods: list[str]
    premium_destination: dict[str, Any] | None
    destination_area: str | None
    pickup_area: str | None
    receipt_requested: bool
    receipt_requested_at: str | None


class SettlementPreviewOut(BaseModel):
    """`POST /admin/settlement/preview` — what a run *would* do, and proof of it.

    `confirm_token` is the only way to run a settlement. It is signed over this
    preview's period and fee (`app/services/ledger/settlement_confirm.py`), so a token
    issued for one week's numbers cannot be spent on another's.

    The outcome fields are all **counts or ids**, never names. A preview is a
    screenful; inlining driver identities would make the safest page in the
    console the largest PII export in it.

    `would_charge + already_charged + tampered + skipped_no_deposit_account`
    equals `eligible_drivers` exactly — pinned by a test, because a report whose
    parts do not sum to its whole is a report someone will misread.

    `would_go_negative` is a **sub**-count of `would_charge`, not a fifth bucket:
    those drivers are charged, they simply go into arrears. Arrears are allowed
    by design (a driver in arrears keeps dispatching and settles on the next
    top-up), so this is the number an operator most needs before pressing the
    button — it is the part that is a decision rather than arithmetic.

    `confirm_expires_in_seconds` is returned so the console can show a countdown
    rather than surprising the operator with an expired token at the moment they
    were ready to act.
    """

    period: str
    fee_hkd: str
    eligible_drivers: int
    fleet_managed: int
    would_charge: int
    already_charged: int
    tampered: int
    skipped_no_deposit_account: int
    would_go_negative: int
    shortfall_total_hkd: str
    total_charge_hkd: str
    would_charge_driver_ids: list[str]
    would_go_negative_driver_ids: list[str]
    confirm_token: str
    confirm_expires_in_seconds: int


class DisputeMessageOut(BaseModel):
    """One turn in a dispute thread.

    `is_internal` is on the wire so the console can render the distinction it
    is required to make — a staff note must not be displayed in a view a party
    can see. Sending the flag and filtering in the UI is deliberate: the
    alternative is two endpoints whose difference is invisible to the reader.

    `author_label` is denormalised at write time because the author may be
    renamed or removed, and a thread that loses its speakers is unreadable
    evidence.
    """

    id: int
    author_kind: str
    author_id: str | None
    author_label: str | None
    body: str
    is_internal: bool
    created_at: str


class AdminDisputeRowOut(BaseModel):
    """One row of `GET /admin/disputes` — the queue.

    Every enum is sent as its **string value**, plus the derived fields the
    queue is sorted and coloured by, so the console does not re-implement
    severity→SLA or the overdue test. Two implementations of "is this late"
    that drift is how a dashboard starts disagreeing with the API.

    `sla_hours` is the budget the case was *given*, and `seconds_until_due` is
    what is left. Both are present because a negative `seconds_until_due` says
    "late" while `sla_hours` says how late by comparison — an operator triaging
    needs the second number to decide whether to escalate.

    `order_id` is nullable and that is meaningful, not missing: account- and
    app-level complaints have no trip.
    """

    id: str
    order_id: str | None
    source: str
    category: str
    severity: str
    status: str
    summary: str
    raised_by_kind: str
    against_kind: str | None
    assigned_admin_id: str | None
    safety_flag: bool
    sla_due_at: str
    sla_hours: int
    seconds_until_due: int
    is_overdue: bool
    resolution: str | None
    resolved_at: str | None
    created_at: str


class AdminDisputePageOut(BaseModel):
    """Paged dispute queue. `items` is typed, not `list[Any]`."""

    items: list[AdminDisputeRowOut]
    total: int
    limit: int
    offset: int


class DisputeStatsOut(BaseModel):
    """The counts the queue header shows, computed server-side.

    With pagination a client-side count is the count of the current page, which
    reads as the total. `overdue` is therefore derived here, against the same
    clock the sort uses.
    """

    total: int
    open: int
    overdue: int
    unassigned: int
    safety_flag: int


class AdminDisputeDetailOut(AdminDisputeRowOut):
    """A dispute plus its thread.

    Inherits the row rather than re-declaring it: the detail page shows the same
    header as the queue, and two definitions would drift into a list that
    disagrees with the page it opens.

    `messages` is **every** message including internal ones, because this
    endpoint is admin-only. A party-facing view would be a different endpoint
    with a different filter — not this one with a query flag, which is a filter
    somebody eventually forgets.

    The arrival-evidence fields are optional on the wire: they belong to the
    order, not to the dispute, and a case opened without an order (or from a
    build before P4 recorded the evidence) should still render.
    """

    messages: list[DisputeMessageOut]
    resolution_note: str | None
    resolved_by: str | None
    arrival_claimed_at: str | None = None
    arrival_gps_distance_m: str | None = None
    arrival_pin_attempts: int | None = None


class DisputeResolveOut(BaseModel):
    """The outcome of a resolution decision.

    `moves_money` is echoed so the console can prompt for the follow-up ledger
    action rather than inferring it from `resolution` — the inference is
    exactly the kind of duplicated enum knowledge that goes stale.
    """

    id: str
    status: str
    resolution: str
    moves_money: bool
    resolved_at: str


class AdminSearchResultOut(BaseModel):
    """One subject: a passenger or a driver, in a single shape.

    A **superset** of what either kind has, with the irrelevant fields null
    rather than absent. A discriminated union would be more precise and worse
    here: the console renders one list, and `item.plate ?? "—"` is the whole
    rendering either way. `kind` is what the UI branches on.

    Carries the phone and display name because those are what the caller
    searched with. Deliberately **no** ID number, no document keys and no
    ledger: a search result is a pointer, and the detail page is the audited
    place to look at a person. Otherwise every keystroke becomes a bulk PII read
    with no audit line.
    """

    kind: str  # PASSENGER | DRIVER
    id: str
    display_name: str | None
    phone_e164: str
    username: str | None
    account_status: str
    is_active: bool
    avatar_key: str | None
    driver_profile_id: str | None
    plate: str | None
    driver_status: str | None


class AdminSearchOut(BaseModel):
    """Search results plus the two facts the UI must state.

    `truncated` is not derivable from `len(items) == limit` — a result set that
    is exactly `limit` long may or may not have more, and "showing 20 of 20"
    when there are 400 is the difference between narrowing the search and
    believing you have seen everyone.

    `query_too_short` distinguishes "no matches" from "you did not search",
    which are otherwise the same empty list. Telling an operator that a
    two-character name has no match when they typed one character is how a
    working search gets reported as broken.
    """

    items: list[AdminSearchResultOut]
    query: str
    truncated: bool
    query_too_short: bool
    min_query_length: int


class AdminLiveDriverOut(BaseModel):
    """One car on the live map: where it is, and what it is doing.

    `order_id` / `order_status` are null for a driver who is online but
    unassigned — the normal state for most of the fleet at any moment. They are
    flat siblings rather than a nested object so the console's "running
    vehicles" filter is `row.order_id !== null`, not a null check on a
    sub-object that a serializer might materialise as `{}`.

    `vehicle_reg_mark` is here because an operator identifies a car by its
    plate, not by a UUID. It is already exposed by `DriverRowOut`, so this adds
    no new disclosure.

    Deliberately absent: `hk_id_last4` and every document key. A map needs to
    know *where* a car is; the audited detail page is where a person is looked
    at.

    `lat` / `lng` are not optional. The query filters `current_location IS NOT
    NULL`, so a row without a fix cannot reach this model — and a marker with no
    position is not a thing the map could draw anyway.
    """

    driver_profile_id: str
    status: str
    taxi_type: str
    vehicle_reg_mark: str
    is_online: bool
    last_location_at: str | None
    lat: float
    lng: float
    order_id: str | None
    order_status: str | None


class AdminLiveDriversOut(BaseModel):
    """`GET /admin/live/drivers` — the whole map in one poll.

    `generated_at` is the server clock at the moment of the read, not the newest
    `last_location_at`. The console needs both, and they answer different
    questions: the first says how fresh the *snapshot* is, the second how stale
    one individual car is. A car whose fix predates the poll interval is drawn
    differently from one that moved a second ago, and that distinction is lost
    if the payload only carries one timestamp.

    `truncated` is stated for the same reason as in `AdminSearchOut`: a map that
    silently drops the 501st car is indistinguishable from a fleet that shrank.
    """

    generated_at: str
    drivers: list[AdminLiveDriverOut]
    truncated: bool

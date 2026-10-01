"""Admin API: KYC review queue + decisions + deposit grants. ADMIN enforced.

Hardening: require_admin now re-reads the live user row (P0-3). Deposit grants
are idempotent when the client supplies `reference` (P1-7): a retried grant
replays the original entry instead of double-crediting. Driver listing is
paginated (P1-8).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import (
    AdminAccountCreatedOut,
    AdminAccountPageOut,
    AdminDisputeDetailOut,
    AdminDisputePageOut,
    AdminOrderDetailOut,
    AdminOrderPageOut,
    AdminPasswordResetOut,
    AdminRoleChangeOut,
    AuditPageOut,
    DepositAdjustOut,
    DepositGrantOut,
    DisputeMessageOut,
    DisputeResolveOut,
    DisputeStatsOut,
    DriverDetailOut,
    DriverPageOut,
    DriverReviewOut,
    RefundDecisionOut,
    RefundPageOut,
    SettlementPreviewOut,
    SettlementRunOut,
)
from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
from app.core.deps import Principal, live_admin_role, require_admin, require_role
from app.core.exceptions import BusinessRuleError
from app.core.money import meter_str, money_str
from app.models import (
    AdminAccount,
    AdminAuditLog,
    AdminRole,
    DisputeCategory,
    DisputeMessage,
    DisputeResolution,
    DisputeSeverity,
    DisputeSource,
    DisputeStatus,
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    Fleet,
    FleetMembership,
    FleetMemberStatus,
    LedgerEntry,
    LedgerEntryType,
    Order,
    OrderDispute,
    OrderStatus,
    RefundRequest,
    RefundStatus,
)
from app.services.admin_account_service import AdminAccountService
from app.services.audit_service import (
    EV_ADMIN_ACCOUNT_CREATE,
    EV_ADMIN_PASSWORD_RESET,
    EV_ADMIN_ROLE_CHANGE,
    EV_DEPOSIT_ADJUST,
    EV_DEPOSIT_GRANT,
    EV_DISPUTE_ASSIGN,
    EV_DISPUTE_CREATE,
    EV_DISPUTE_MESSAGE,
    EV_DISPUTE_RESOLVE,
    EV_KYC_DECISION,
    EV_REFUND_DECISION,
    EV_SETTLEMENT_PREVIEW,
    EV_SETTLEMENT_RUN,
    OUTCOME_SUCCESS,
    record_audit,
)
from app.services.dispute_service import DisputeService, case_is_overdue
from app.services.ledger_service import (
    LedgerService,
    reference_for_adjustment,
    reference_for_grant,
)
from app.services.refund_service import RefundService
from app.services.settlement_confirm import (
    PREVIEW_TTL_SECONDS,
    issue_confirm_token,
    verify_confirm_token,
)
from app.services.settlement_service import SettlementService, period_key, period_start
from app.services.state_machine import assert_driver_transition

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Who may do what, expressed once. Read endpoints stay on `require_admin` —
# every role may look. Only the write endpoints below are narrowed, so this
# change is purely additive: a route that missed a guard is merely wide, never
# open, and the route-table audit in `tests/test_security_hardening.py` proves
# each one still carries a live-state guard.
#
#   KYC / driver state  -> OPERATIONS  (a compliance judgement, not a money one)
#   money movement      -> FINANCE     (grant, adjust, settlement, refund)
#   dispute judgement   -> OPERATIONS  (conduct is not a money act)
#   dispute *resolution that moves money* -> FINANCE, checked per-request
#
# The dispute split is the one case where the role is not a fixed property of
# the route: `POST /disputes/{id}/resolve` is reachable by OPERATIONS, but a
# resolution whose `moves_money` is true additionally requires FINANCE. The
# reason is separation of duties — whoever judges that a driver behaved badly
# must not thereby authorise the payout. Enforced in the handler because the
# decision is in the body, not the path.
#
# Deliberately NOT `SUPPORT`: answering a question and deciding one are
# different jobs, and merged, front-line support inherits the KYC gate.
_require_operations = require_role(AdminRole.OPERATIONS)
_require_finance = require_role(AdminRole.FINANCE)


class DriverReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject|suspend|terminate)$")
    note: str = ""


_DECISION_TARGET = {
    "approve": DriverStatus.DEPOSIT_REQUIRED,
    "reject": DriverStatus.TERMINATED,
    "suspend": DriverStatus.SUSPENDED,
    "terminate": DriverStatus.TERMINATED,
}


@router.get("/drivers", response_model=DriverPageOut)
async def list_drivers(
    status_filter: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = select(DriverProfile).order_by(DriverProfile.created_at)
    if status_filter:
        q = q.where(DriverProfile.status == DriverStatus(status_filter))
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(select(func.count()).select_from(DriverProfile))).scalar_one()
    return {
        "items": [
            {
                "id": str(dp.id),
                "status": dp.status.value,
                "taxi_type": dp.taxi_type,
                "taxi_driver_plate_no": dp.taxi_driver_plate_no,
                "vehicle_reg_mark": dp.vehicle_reg_mark,
            }
            for dp in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _driver_detail_out(
    dp: DriverProfile,
    deposit: DriverDeposit | None,
    *,
    ledger: list[LedgerEntry],
    refunds: list[RefundRequest],
    membership: FleetMembership | None,
    fleet: Fleet | None,
) -> dict:
    """One driver, as the console's detail page needs them.

    The list endpoint (`GET /admin/drivers`) carries only what the KYC queue
    renders in a row. This is the "everything an operator has to look at before
    deciding" view: the identity documents, the deposit and its required
    threshold, the statement, the refund history, and the fleet the driver is
    currently billed under.

    `hk_id_last4` is returned **masked** — an admin who needs to check the
    document against a person needs the last four digits, not the whole number,
    and the unmasked form never leaves the database.
    """
    return {
        "id": str(dp.id),
        "user_id": str(dp.user_id),
        "status": dp.status.value,
        "taxi_type": dp.taxi_type,
        "taxi_driver_plate_no": dp.taxi_driver_plate_no,
        "vehicle_reg_mark": dp.vehicle_reg_mark,
        "hk_id_last4": dp.hk_id_last4,
        "is_online": dp.is_online,
        "kyc_reviewed_at": dp.kyc_reviewed_at.isoformat() if dp.kyc_reviewed_at else None,
        "created_at": dp.created_at.isoformat() if dp.created_at else None,
        "updated_at": dp.updated_at.isoformat() if dp.updated_at else None,
        "deposit": _deposit_detail_out(deposit),
        "ledger": {
            "items": [_ledger_out(entry) for entry in ledger],
        },
        "refunds": {"items": [_refund_out(r) for r in refunds]},
        "fleet": (
            {
                "fleet_id": str(fleet.id),
                "name": fleet.name,
                "license_no": fleet.license_no,
                "status": fleet.status.value,
                "weekly_fee_discount_percent": str(Decimal(fleet.weekly_fee_discount_percent)),
                "member_role": membership.member_role.value,
                "joined_at": membership.joined_at.isoformat() if membership.joined_at else None,
            }
            if fleet is not None and membership is not None
            else None
        ),
    }


def _deposit_detail_out(dep: DriverDeposit | None) -> dict:
    """The deposit as the detail page shows it — with the shortfall spelled out.

    A driver with no deposit row has never been credited, which is a different
    situation from a zero balance; both are rendered, but `required_hkd` is still
    the platform default so the page can show progress against a real target.

    The two branches emit the **same keys**: the page reads `shortfall_hkd`
    unconditionally, so a branch that omitted it would leave the "距達標" cell
    undefined on exactly the driver who most needs it — one who has never paid.
    """
    required = Decimal(dep.required_hkd) if dep is not None else Decimal("500")
    balance = Decimal(dep.balance_hkd) if dep is not None else Decimal(0)
    return {
        "balance_hkd": money_str(balance),
        # `money_str(0)`, not the literal "0.0": this branch fires for every
        # driver who has never been credited, so a 1-dp literal here would put
        # the only 1-dp money value on the page next to four 2-dp ones.
        "held_hkd": money_str(Decimal(dep.held_hkd)) if dep is not None else money_str(Decimal(0)),
        "required_hkd": money_str(required),
        "is_fulfilled": dep.is_fulfilled if dep is not None else False,
        "has_account": dep is not None,
        "shortfall_hkd": money_str(max(required - balance, Decimal(0))),
    }


def _ledger_out(entry: LedgerEntry) -> dict:
    """Ledger row as the admin detail page shows it.

    `created_by` is exposed here and deliberately NOT on the driver-facing
    `/drivers/me/ledger`: an operator's user id is internal, but for an
    `ADJUSTMENT` — a discretionary move with no upstream event — attribution is
    the point of keeping the record at all. Automated entries carry NULL.
    """
    return {
        "id": entry.id,
        "entry_type": entry.entry_type.value,
        "amount_hkd": money_str(Decimal(entry.amount_hkd)),
        "balance_after_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "order_id": str(entry.order_id) if entry.order_id else None,
        "note": entry.note,
        "reference": entry.reference,
        "created_by": str(entry.created_by) if entry.created_by else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/drivers/{driver_id}", response_model=DriverDetailOut)
async def driver_detail(
    driver_id: str,
    ledger_limit: Annotated[int, Query(ge=1, le=200)] = 50,
    refund_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Everything about one driver, in one round trip.

    Composed server-side rather than left to the console: a page that makes five
    calls can render a half-truthful driver (the statement loaded, the deposit
    not), and the console has no way to say which. One call, one consistent read.

    Ledger and refund history are capped, newest first — the statement grows
    without bound, and an operator looking at a driver is interested in the
    recent position, not the full archive.
    """
    dp = await session.get(DriverProfile, driver_id)
    if dp is None:
        raise HTTPException(status_code=404, detail="driver not found")

    deposit = (
        (
            await session.execute(
                select(DriverDeposit).where(DriverDeposit.driver_profile_id == dp.id)
            )
        )
        .scalars()
        .first()
    )

    ledger = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.driver_profile_id == dp.id)
                .order_by(LedgerEntry.id.desc())
                .limit(ledger_limit)
            )
        )
        .scalars()
        .all()
    )

    refunds = (
        (
            await session.execute(
                select(RefundRequest)
                .where(RefundRequest.driver_profile_id == dp.id)
                .order_by(RefundRequest.created_at.desc())
                .limit(refund_limit)
            )
        )
        .scalars()
        .all()
    )

    membership = (
        (
            await session.execute(
                select(FleetMembership).where(
                    FleetMembership.driver_profile_id == dp.id,
                    FleetMembership.status == FleetMemberStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .first()
    )
    fleet = await session.get(Fleet, membership.fleet_id) if membership else None

    return _driver_detail_out(
        dp,
        deposit,
        ledger=list(ledger),
        refunds=list(refunds),
        membership=membership,
        fleet=fleet,
    )


@router.post("/drivers/{driver_id}/review", response_model=DriverReviewOut)
async def review_driver(
    driver_id: str,
    payload: DriverReviewIn,
    request: Request,
    admin: Principal = Depends(_require_operations),
    session: AsyncSession = Depends(get_session),
):
    dp = await session.get(DriverProfile, driver_id)
    if dp is None:
        raise HTTPException(status_code=404, detail="driver not found")
    target = _DECISION_TARGET[payload.decision]
    assert_driver_transition(dp.status, target)
    previous = dp.status.value
    dp.status = target
    dp.kyc_reviewed_by = admin.id
    dp.kyc_reviewed_at = datetime.now(UTC)
    # Same session, so the audit row commits or rolls back with the decision it
    # records. An audit entry for a rollback would be worse than none.
    await record_audit(
        session,
        event=EV_KYC_DECISION,
        actor_id=admin.id,
        detail=f"{payload.decision} driver {dp.id}",
        request=request,
        payload={
            "driver_profile_id": str(dp.id),
            "decision": payload.decision,
            "before": {"status": previous},
            "after": {"status": target.value},
            "note": payload.note or None,
        },
    )
    await session.flush()
    return {"id": str(dp.id), "status": dp.status.value}


class DepositGrantIn(BaseModel):
    amount_hkd: Decimal = Field(gt=0, le=100000)
    note: str = ""
    # P1-7 idempotency: client-supplied key; a retry with the same key replays.
    # SEC-13: it is namespaced server-side (see reference_for_grant) and never
    # used verbatim as the ledger reference.
    reference: str | None = Field(default=None, max_length=120)


@router.post("/drivers/{driver_id}/deposit/grant", response_model=DepositGrantOut)
async def grant_deposit(
    driver_id: str,
    payload: DepositGrantIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    dp = await session.get(DriverProfile, driver_id)
    if dp is None:
        raise HTTPException(status_code=404, detail="driver not found")

    # SEC-13: the client key is namespaced under `grant:<driver>:` so it can never
    # be crafted to collide with a settlement (`weekly:`) or refund (`refund:`)
    # reference. Retry-idempotency for the caller is preserved.
    reference = reference_for_grant(dp.id, payload.reference)
    deposit = await LedgerService.ensure_deposit_row(session, dp)
    entry = await LedgerService(session).append(
        driver_profile_id=dp.id,
        entry_type=LedgerEntryType.DEPOSIT_TOPUP,
        amount_hkd=payload.amount_hkd,
        note=payload.note or "admin deposit grant",
        created_by=admin.id,
        reference=reference,
    )

    # Fulfilment activates the driver (DEPOSIT_REQUIRED -> ACTIVE).
    activated = False
    if deposit.is_fulfilled and dp.status == DriverStatus.DEPOSIT_REQUIRED:
        assert_driver_transition(dp.status, DriverStatus.ACTIVE)
        dp.status = DriverStatus.ACTIVE
        activated = True

    await record_audit(
        session,
        event=EV_DEPOSIT_GRANT,
        actor_id=admin.id,
        detail=f"grant {money_str(payload.amount_hkd)} to driver {dp.id}",
        request=request,
        payload={
            "driver_profile_id": str(dp.id),
            "amount_hkd": money_str(payload.amount_hkd),
            "balance_after_hkd": money_str(Decimal(entry.balance_after_hkd)),
            "reference": entry.reference,
            "note": payload.note or None,
            # Recorded because it is a side effect an operator may not realise
            # this endpoint has: a grant can move the driver to ACTIVE.
            "activated_driver": activated,
        },
    )

    return {
        "id": str(dp.id),
        "driver_status": dp.status.value,
        "balance_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "is_fulfilled": deposit.is_fulfilled,
        "reference": entry.reference,
    }


class DepositAdjustIn(BaseModel):
    """A manual correction to a driver's balance (ledger `ADJUSTMENT`).

    `amount_hkd` is **signed**: positive credits the driver (we under-charged, or
    a goodwill gesture), negative debits them (we over-charged, or a charge the
    automated flows never posted). It is deliberately not `gt=0` like a grant.
    """

    amount_hkd: Decimal = Field(ge=-5000, le=5000)
    # Required, not optional: an adjustment has no upstream event to point at,
    # so the reason *is* the audit trail. An unexplained balance change is worse
    # than no adjustment tool at all.
    reason: str = Field(min_length=3, max_length=500)
    reference: str | None = Field(default=None, max_length=60)


@router.post("/drivers/{driver_id}/deposit/adjust", response_model=DepositAdjustOut)
async def adjust_deposit(
    driver_id: str,
    payload: DepositAdjustIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    """Post a manual `ADJUSTMENT` to a driver's deposit ledger.

    Why this exists: every other ledger writer is tied to an event — a grant has
    a payment, a weekly fee has a period, a refund has a request. When the books
    need correcting for something with no such event, the only honest option was
    a raw `DEPOSIT_TOPUP` grant, which lies about *why* the money moved and
    inflates the top-up total on every report that sums it.

    Bounded at ±HK$5,000 on purpose. An adjustment is a correction, not a
    payment channel; anything larger is a decision that belongs to a human
    reviewing a real reconciliation, not a number typed into a form. Negative
    balances remain legal (arrears) — see LedgerService.append.

    The deposit row is created if absent, matching `grant`: an operator
    correcting a driver who was never credited should not have to grant first
    and then adjust, which would leave two entries where one is needed.
    ``ensure_deposit_row`` flushes, and ``append`` row-locks it before touching
    the balance, so this is safe under concurrency.
    """
    dp = await session.get(DriverProfile, driver_id)
    if dp is None:
        raise HTTPException(status_code=404, detail="driver not found")

    # SEC-13: `adj:` prefix, so this can never collide with grant/weekly/fleet/
    # refund references and silently swallow one of them.
    reference = reference_for_adjustment(dp.id, payload.reason, payload.reference)
    deposit = await LedgerService.ensure_deposit_row(session, dp)
    entry = await LedgerService(session).append(
        driver_profile_id=dp.id,
        entry_type=LedgerEntryType.ADJUSTMENT,
        amount_hkd=payload.amount_hkd,
        note=payload.reason,
        created_by=admin.id,
        reference=reference,
    )

    # The `reason` is already mandatory on the request and is the human
    # explanation, but it lives only on the ledger row — which is visible only
    # to someone already reading that driver's ledger. The audit row is what
    # makes a manual balance change findable by *who did it* rather than by
    # *whose balance moved*, which is the direction an investigation runs.
    await record_audit(
        session,
        event=EV_DEPOSIT_ADJUST,
        actor_id=admin.id,
        detail=f"adjust {money_str(payload.amount_hkd)} on driver {dp.id}: {payload.reason}"[:255],
        request=request,
        payload={
            "driver_profile_id": str(dp.id),
            "amount_hkd": money_str(payload.amount_hkd),
            "balance_after_hkd": money_str(Decimal(entry.balance_after_hkd)),
            "reference": entry.reference,
            "reason": payload.reason,
        },
    )

    return {
        "id": str(dp.id),
        "driver_status": dp.status.value,
        "amount_hkd": money_str(Decimal(entry.amount_hkd)),
        "balance_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "is_fulfilled": deposit.is_fulfilled,
        "reference": entry.reference,
    }


class SettlementPreviewIn(BaseModel):
    """The preview request body. Only `period` — the fee comes from settings.

    The fee is deliberately **not** client-supplied. `run_weekly` reads it from
    config, so if a caller could preview at an arbitrary fee, the two numbers on
    the confirm screen and on the charge would be able to disagree — which is
    the exact confusion the confirmation token exists to eliminate.
    """

    period: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$")


@router.post("/settlement/preview", response_model=SettlementPreviewOut)
async def preview_weekly_settlement(
    payload: SettlementPreviewIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Dry-run the weekly settlement. Writes nothing, charges nobody.

    FINANCE, same as the run: a preview that only one role could obtain and
    another could act on is a workflow nobody can complete.
    """
    settings = get_settings()
    result = await SettlementService(session_factory).preview_weekly(
        settings.weekly_fee_hkd, payload.period
    )
    # Bind the token to the fee *as the preview rendered it* — `result["fee_hkd"]`
    # — not to `str(settings.weekly_fee_hkd)`. The two differ whenever the
    # configured fee is not already 2 dp (`200` vs `200.00`), and the run
    # verifies against the latter. Minting one and verifying the other refuses
    # every token the preview ever hands out, with a message naming a fee the
    # operator never saw. One source of truth for the fee string.
    token = issue_confirm_token(period=result["period"], fee_hkd=result["fee_hkd"])
    await record_audit_preview(session_factory, admin=admin, request=request, result=result)
    return {**result, "confirm_token": token, "confirm_expires_in_seconds": PREVIEW_TTL_SECONDS}


async def record_audit_preview(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    admin: Principal,
    request: Request,
    result: dict,
) -> None:
    """Log that a settlement was previewed. Best-effort, own session.

    Separate from `record_audit`'s other callers because the preview has **no
    transaction of its own to ride on** — it writes nothing — so a row here
    always needs a fresh session. Recorded at all because "who looked at the
    numbers before the money moved" is the first question after a bad run.
    """
    async with session_factory() as audit_session:
        await record_audit(
            audit_session,
            event=EV_SETTLEMENT_PREVIEW,
            actor_id=admin.id,
            detail=f"previewed weekly settlement for {result['period']}",
            payload={
                "period": result["period"],
                "fee_hkd": result["fee_hkd"],
                "would_charge": result["would_charge"],
                "already_charged": result["already_charged"],
                "would_go_negative": result["would_go_negative"],
                "total_charge_hkd": result["total_charge_hkd"],
            },
            request=request,
        )
        await audit_session.commit()


@router.post("/settlement/weekly/run", response_model=SettlementRunOut)
async def run_weekly_settlement(
    request: Request,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-W\d{2}$")] = None,
    confirm_token: Annotated[str | None, Query(max_length=2048)] = None,
    admin: Principal = Depends(_require_finance),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Run the weekly service-fee settlement by hand (idempotent per ISO week).

    The scheduled job fires once at boot then every 7 days; this is the ops lever
    to re-run a specific period (e.g. after a failed batch) without touching the
    database. Re-running an already-settled period charges nobody.

    **Requires a `confirm_token` from `/settlement/preview`.** This button
    charges every eligible driver at once, and it is idempotent per ISO week —
    which means the *first* accidental press is not something you get to undo,
    because the money is gone and the reference is spent. Requiring a token the
    preview issued is what turns "look before you leap" from a runbook line into
    a structural constraint.

    Deliberately **not** required when the caller targets a period that has
    already been settled: that run is a no-op by construction, and demanding a
    preview to prove nothing will happen would train operators to click through
    the gate rather than read it.

    The token binds the **period and the fee**, not the actor. Two finance
    admins working the same incident should not have to pass a token back and
    forth, and both actions are audited with their own actor anyway.
    """
    settings = get_settings()
    service = SettlementService(session_factory)

    # Settle the period the token was issued for, and refuse a mismatch rather
    # than silently using one or the other. `period` on the query string is the
    # operator's intent; the token is what was previewed. If they disagree, one
    # of the two is a mistake and guessing which is how a wrong week gets charged.
    if confirm_token:
        wanted = period or period_key()
        verify_confirm_token(
            confirm_token,
            period=wanted,
            # Same normalization as the mint above: `Settings.weekly_fee_hkd`
            # is an int, and an int and its 2 dp rendering are different
            # strings.
            fee_hkd=money_str(settings.weekly_fee_hkd),
        )
    else:
        # No token: only allowed if this exact period is already fully settled,
        # which we can only know by checking. Ask the preview, which writes
        # nothing, and refuse if it says there is anything left to charge.
        wanted = period or period_key()
        check = await service.preview_weekly(settings.weekly_fee_hkd, wanted)
        pending = check["would_charge"]
        if pending:
            raise BusinessRuleError(
                "a settlement run requires a confirmation token from /settlement/preview",
                {
                    "reason": "CONFIRM_TOKEN_REQUIRED",
                    "period": wanted,
                    "would_charge": pending,
                    "hint": "call POST /api/v1/admin/settlement/preview first",
                },
            )
        period = wanted

    result = await service.run_weekly(settings.weekly_fee_hkd, period)
    # Audited on its own session, deliberately. `run_weekly` commits per driver
    # in its own transactions, so by the time this returns the money has already
    # moved — writing the audit row on a session that could still be rolled back
    # would leave real charges with no record of who ran them. Recording after
    # the fact is the honest ordering here; the alternative is an audit row for
    # a run that did not happen.
    async with session_factory() as audit_session:
        await record_audit(
            audit_session,
            event=EV_SETTLEMENT_RUN,
            actor_id=admin.id,
            detail=f"weekly settlement run for {period or 'current period'}",
            payload={
                "period": period or result.get("period"),
                "weekly_fee_hkd": money_str(settings.weekly_fee_hkd),
                "confirmed_by_token": bool(confirm_token),
                "result": {
                    k: (money_str(v) if isinstance(v, Decimal) else v)
                    for k, v in (result or {}).items()
                },
            },
            request=request,
        )
        await audit_session.commit()
    return result


@router.get(
    "/settlement/export.csv",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {"schema": {"type": "string"}}},
            "description": "Ledger rows for the ISO week, as CSV.",
        }
    },
)
async def export_settlement_csv(
    period: Annotated[str, Query(pattern=r"^\d{4}-W\d{2}$")],
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    """Every ledger entry for `period`, as CSV, for the finance handover.

    FINANCE only. The row set is every fee-bearing entry of the week, so this is
    the whole week's billing in one file — a SUPPORT account has no reason to
    have it.

    `text/csv` with an explicit filename rather than JSON, because the consumer
    is a spreadsheet. The columns are chosen for a reconciliation someone does
    by eye: the driver, the direction and amount, the resulting balance, and the
    reference that makes the row traceable back to the job that wrote it.
    """
    rows = (
        await session.execute(
            select(LedgerEntry, DriverProfile)
            .join(DriverProfile, DriverProfile.id == LedgerEntry.driver_profile_id)
            .where(
                LedgerEntry.created_at >= period_start(period),
                LedgerEntry.created_at < period_start(period) + timedelta(days=7),
                LedgerEntry.entry_type.in_(
                    (
                        LedgerEntryType.WEEKLY_FEE_DEDUCTION,
                        LedgerEntryType.PENALTY_DEDUCTION,
                        LedgerEntryType.ADJUSTMENT,
                        LedgerEntryType.REFUND,
                        LedgerEntryType.DEPOSIT_TOPUP,
                    )
                ),
            )
            .order_by(LedgerEntry.created_at)
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "entry_id",
            "created_at",
            "driver_profile_id",
            "vehicle_reg_mark",
            "entry_type",
            "amount_hkd",
            "balance_after_hkd",
            "order_id",
            "reference",
            "note",
        ]
    )
    total = Decimal("0")
    for entry, driver in rows:
        amount = Decimal(entry.amount_hkd)
        total += amount
        writer.writerow(
            [
                entry.id,
                entry.created_at.isoformat() if entry.created_at else "",
                str(entry.driver_profile_id),
                driver.vehicle_reg_mark,
                entry.entry_type.value,
                money_str(amount),
                money_str(Decimal(entry.balance_after_hkd)),
                str(entry.order_id) if entry.order_id else "",
                entry.reference or "",
                (entry.note or "").replace("\n", " "),
            ]
        )
    # A trailing TOTAL row rather than a header comment: a spreadsheet that sums
    # the column and gets a different answer than the file claims is a support
    # ticket, and the difference is usually a row someone filtered out.
    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", "", money_str(total), "", "", "", ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="settlement-{period}.csv"'},
    )


def _refund_out(r: RefundRequest) -> dict:
    return {
        "id": str(r.id),
        "driver_profile_id": str(r.driver_profile_id),
        "amount_hkd": money_str(Decimal(r.amount_hkd)),
        "status": r.status.value,
        "note": r.note,
        "decision_note": r.decision_note,
        "decided_by": str(r.decided_by) if r.decided_by else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/refunds", response_model=RefundPageOut)
async def list_refunds(
    status_filter: Annotated[str | None, Query(pattern=r"^(PENDING|APPROVED|REJECTED)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Refund queue. Default ordering is newest-first so PENDING rows surface."""
    q = select(RefundRequest).order_by(RefundRequest.created_at.desc())
    count_q = select(func.count()).select_from(RefundRequest)
    if status_filter:
        q = q.where(RefundRequest.status == RefundStatus(status_filter))
        count_q = count_q.where(RefundRequest.status == RefundStatus(status_filter))
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_refund_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class RefundDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = ""


@router.post("/refunds/{refund_id}/decision", response_model=RefundDecisionOut)
async def decide_refund(
    refund_id: uuid.UUID,
    payload: RefundDecisionIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    """Approve (pays out, driver TERMINATED) or reject (releases hold, driver ACTIVE).

    Approval is the only path that moves money out: it writes the REFUND ledger
    entry keyed `refund:{id}`, so a double-click cannot pay twice. A second
    decision on an already-decided request is rejected.
    """
    refund = await RefundService(session).decide(
        refund_id,
        approve=payload.decision == "approve",
        admin_id=admin.id,
        decision_note=payload.note or None,
    )
    # Both outcomes are audited, not just the payout. A refusal is also a
    # decision somebody may later have to justify, and it leaves no ledger trace
    # at all — which is precisely why `refund.decided_by` alone was not enough.
    await record_audit(
        session,
        event=EV_REFUND_DECISION,
        actor_id=admin.id,
        detail=f"{payload.decision} refund {refund_id}",
        request=request,
        payload={
            "refund_id": str(refund_id),
            "driver_profile_id": str(refund.driver_profile_id),
            "decision": payload.decision,
            "amount_hkd": money_str(Decimal(refund.amount_hkd)),
            "note": payload.note or None,
        },
    )
    return _refund_out(refund)


def _audit_out(row: AdminAuditLog) -> dict:
    return {
        "id": str(row.id),
        "actor_id": str(row.admin_id) if row.admin_id else None,
        "actor_username": row.username_attempted,
        "event": row.event,
        "outcome": row.outcome,
        "detail": row.detail,
        "payload": row.payload,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("/audit", response_model=AuditPageOut)
async def list_audit_log(
    event: Annotated[str | None, Query(max_length=48)] = None,
    admin_id: uuid.UUID | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Read the audit trail. Newest first, filterable by event / actor / range.

    Readable by **every** admin role, including SUPPORT, and that is
    intentional. The audit log is a record of what was done by whom; restricting
    its reading would mean the people least able to change anything are also the
    least able to notice that something was changed. It contains no secrets —
    no password, no TOTP secret, no token — only decisions and their actors.

    `since`/`until` are half-open (`created_at >= since`, `< until`), matching
    the analytics range convention, so an operator paging day by day does not
    see the boundary row twice.
    """
    q = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
    count_q = select(func.count()).select_from(AdminAuditLog)
    filters = []
    if event:
        filters.append(AdminAuditLog.event == event)
    if admin_id is not None:
        filters.append(AdminAuditLog.admin_id == admin_id)
    if since is not None:
        filters.append(AdminAuditLog.created_at >= since)
    if until is not None:
        filters.append(AdminAuditLog.created_at < until)
    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_audit_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------
# Admin account management. SUPER_ADMIN only, all four routes.
#
# These are the routes that decide who may do everything else, so they are the
# one place where the RBAC hierarchy has to hold without help: `require_role`
# is applied at the route *and* the constraints are re-checked in
# `AdminAccountService`, because an OPERATIONS account promoting itself is the
# single move that makes every other guard meaningless.
# --------------------------------------------------------------------------

_require_super = require_role(AdminRole.SUPER_ADMIN)


async def _actor_username(session: AsyncSession, admin: Principal) -> str | None:
    """The calling admin's username, for the audit row's denormalised copy.

    `Principal` carries an id and a role but no name, and `record_audit`'s
    `username` column is otherwise only populated by the login path. A role
    change is exactly the row where "who did this" has to be legible without a
    join — the whole point of the from/to payload is that it answers the
    question on its own. One extra read on a route that changes an account is
    not a cost worth trading that for.

    Returns `None` rather than raising if the row is gone: the caller is
    authenticated and the action will succeed or fail on its own merits, and a
    missing username must not be the thing that breaks it.
    """
    row = await session.get(AdminAccount, admin.id)
    return row.username if row is not None else None


class AdminAccountCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=3, max_length=254)
    # Password *policy* is enforced in `hash_password` (length, whitespace,
    # predictability); the bound here only stops an unbounded body.
    password: str = Field(min_length=1, max_length=256)
    full_name: str | None = Field(default=None, max_length=120)
    admin_role: AdminRole = AdminRole.SUPPORT


class AdminRoleChangeIn(BaseModel):
    admin_role: AdminRole


class AdminPasswordResetIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


def _account_out(row: AdminAccount) -> dict:
    return {
        "id": str(row.id),
        "username": row.username,
        "email": row.email,
        "full_name": row.full_name,
        "admin_role": row.admin_role.value,
        "is_active": row.is_active,
        # Derived, never the secret: the secret does not leave the server and
        # enrolment is the only fact a client acts on.
        "totp_enrolled": row.totp_secret is not None,
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("/accounts", response_model=AdminAccountPageOut)
async def list_admin_accounts(
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """The admin roster, with the count the demote button depends on.

    SUPER_ADMIN only, unlike `/audit` which is readable by all. The difference
    is the data: an audit row is a decision, an account row is a credential
    holder's identity, and knowing who the super admins are is the
    reconnaissance step before an attack on one of them.
    """
    service = AdminAccountService(session)
    rows = await service.list_accounts()
    return {
        "items": [_account_out(r) for r in rows],
        "super_admin_count": await service.count_super_admins(),
        "total": len(rows),
    }


@router.post("/accounts", response_model=AdminAccountCreatedOut, status_code=201)
async def create_admin_account(
    payload: AdminAccountCreateIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Create an admin account. It must enrol TOTP before it can log in.

    A newly created account has no `totp_secret`, so the login flow routes it
    into enrolment — which is why the response says
    `totp_enrolment_pending: true` rather than leaving the caller to infer it
    from an absent field.
    """
    service = AdminAccountService(session)
    account = await service.create(
        username=payload.username,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role=payload.admin_role,
        created_by=admin.id,
    )
    await record_audit(
        session,
        event=EV_ADMIN_ACCOUNT_CREATE,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"created admin {account.username} as {account.role}",
        payload={
            "created_id": str(account.id),
            "created_username": account.username,
            "admin_role": account.role,
        },
        request=request,
    )
    await session.commit()
    return {**_account_out(account), "totp_enrolment_pending": True}


@router.patch("/accounts/{account_id}/role", response_model=AdminRoleChangeOut)
async def change_admin_role(
    account_id: uuid.UUID,
    payload: AdminRoleChangeIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Change an account's role. Four constraints, all enforced in the service.

    Kept as a `PATCH` on `/role` rather than a general `PATCH /accounts/{id}`
    on purpose: a single update endpoint that accepts `is_active` alongside
    `admin_role` is one where a future field gets added without anyone
    re-reading which constraints applied to the neighbours. Role change is the
    dangerous transition and it gets its own door.
    """
    service = AdminAccountService(session)
    account, previous = await service.change_role(
        account_id=account_id, new_role=payload.admin_role, actor_id=admin.id
    )
    remaining = await service.count_super_admins()
    await record_audit(
        session,
        event=EV_ADMIN_ROLE_CHANGE,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"changed {account.username} from {previous.value} to {account.role}",
        # `from`/`to` rather than the account's current state: the row itself
        # holds only the new value, so the transition exists nowhere else.
        payload={
            "account_id": str(account.id),
            "account_username": account.username,
            "from": previous.value,
            "to": account.role,
            "super_admin_count": remaining,
        },
        request=request,
    )
    await session.commit()
    return {
        "id": str(account.id),
        "previous_role": previous.value,
        "admin_role": account.role,
        "super_admin_count": remaining,
    }


@router.post("/accounts/{account_id}/password/reset", response_model=AdminPasswordResetOut)
async def reset_admin_password(
    account_id: uuid.UUID,
    payload: AdminPasswordResetIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Set another admin's password. SUPER_ADMIN only.

    Changing **your own** password is a different operation with a different
    precondition — it must require the current password, or anyone who finds an
    unlocked session can lock the owner out of their own account. That route is
    not this one; this one exists for the "my only admin is locked out" case,
    which is why it does not require the old password and why it clears the
    lockout counters.
    """
    service = AdminAccountService(session)
    account = await service.reset_password(account_id=account_id, new_password=payload.new_password)
    await record_audit(
        session,
        event=EV_ADMIN_PASSWORD_RESET,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"reset password for {account.username}",
        # Never the password and never the hash — an audit log readable by
        # every role is not a place a credential can appear.
        payload={"account_id": str(account.id), "account_username": account.username},
        request=request,
    )
    await session.commit()
    return {
        "id": str(account.id),
        # The access token is a signed JWT with no server-side session store, so
        # the reset cannot revoke tokens already in flight. Saying so is the
        # honest answer; a boolean that always reads `true` would train the
        # operator to believe a claim the system cannot make.
        "sessions_revoked": False,
    }


# --------------------------------------------------------------------------
# Order monitoring. Read-only, and readable by every role.
#
# The console had no order view at all: `app/api/orders.py` is entirely the
# passenger and driver view, so when a passenger called to say their driver
# never arrived, there was no way to answer "what state is that trip in, who
# is the driver, and how long ago did they accept". Every field below already
# existed on `orders` — nothing new is measured here, it is only surfaced.
#
# No money and no state change, so no `require_role` beyond `require_admin`.
# SUPPORT is exactly who takes that phone call.
# --------------------------------------------------------------------------

_ORDER_OPEN_STATUSES = (
    OrderStatus.CREATED,
    OrderStatus.BROADCASTING,
    OrderStatus.ACCEPTED,
    OrderStatus.DRIVER_ARRIVED,
    OrderStatus.IN_TRIP,
)


def _order_timeline(order: Order) -> list[dict]:
    """The four timestamps in order, with server-computed deltas.

    `elapsed_seconds` is computed here rather than in the client because the
    client would subtract two ISO strings and get it wrong across a DST
    boundary — Hong Kong has no DST today, but a server-side subtraction is
    correct regardless and costs nothing.

    A step whose timestamp is absent is emitted with `at: null` rather than
    omitted, so the console renders a fixed-length timeline and a missing step
    is visibly *missing* instead of silently shorter.
    """
    steps = [
        ("created", order.created_at),
        ("accepted", order.accepted_at),
        ("driver_arrived", order.driver_arrived_at),
        ("completed", order.completed_at),
        ("cancelled", order.cancelled_at),
    ]
    first = order.created_at
    out = []
    for label, at in steps:
        elapsed = None
        if at is not None and first is not None:
            elapsed = int((at - first).total_seconds())
        out.append(
            {
                "step": label,
                "at": at.isoformat() if at else None,
                "elapsed_seconds": elapsed,
            }
        )
    return out


def _admin_order_out(order: Order) -> dict:
    return {
        "id": str(order.id),
        "status": order.status.value,
        "taxi_type": order.taxi_type,
        "passenger_id": str(order.passenger_id),
        "driver_id": str(order.driver_id) if order.driver_id else None,
        "pickup_address": order.pickup_address,
        "dropoff_address": order.dropoff_address,
        "distance_km": meter_str(Decimal(order.distance_km)),
        "estimated_total_hkd": money_str(Decimal(order.estimated_total_hkd)),
        "discount_percent": money_str(Decimal(order.discount_percent or 0)),
        "accepted_at": order.accepted_at.isoformat() if order.accepted_at else None,
        "driver_arrived_at": (
            order.driver_arrived_at.isoformat() if order.driver_arrived_at else None
        ),
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancellation_reason": order.cancellation_reason,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


@router.get("/orders", response_model=AdminOrderPageOut)
async def list_orders(
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    driver_id: uuid.UUID | None = None,
    passenger_id: uuid.UUID | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Order table, newest first, filterable the ways an operator actually asks.

    `open_only` exists because the most common question is not "show me
    cancelled trips" but "show me everything still moving" — the SLA page and
    the live-ops count both want it, and expressing it as five OR'd status
    values belongs on the server where the list of "open" statuses is defined
    next to the enum.

    `status` is validated against `OrderStatus` and a bad value is a 400: a
    filter that silently matches nothing is how an operator concludes there
    are no cancelled trips this week.

    `since`/`until` are half-open on `created_at`, matching `/audit`.
    """
    q = select(Order).order_by(Order.created_at.desc())
    count_q = select(func.count()).select_from(Order)
    filters = []
    if status_filter:
        try:
            filters.append(Order.status == OrderStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "unknown order status",
                    "reason": "UNKNOWN_STATUS",
                    "allowed": [s.value for s in OrderStatus],
                },
            ) from None
    if open_only:
        filters.append(Order.status.in_(_ORDER_OPEN_STATUSES))
    if driver_id is not None:
        filters.append(Order.driver_id == driver_id)
    if passenger_id is not None:
        filters.append(Order.passenger_id == passenger_id)
    if since is not None:
        filters.append(Order.created_at >= since)
    if until is not None:
        filters.append(Order.created_at < until)
    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_admin_order_out(o) for o in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/orders/{order_id}", response_model=AdminOrderDetailOut)
async def order_detail(
    order_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """One trip, in full.

    `fare` is the stored `fare_json` snapshot, not a recomputation. Recomputing
    would produce today's number for a trip quoted under an older tariff — and
    the disputed amount is always the one the passenger was actually quoted,
    which is also why `tariff_version` is returned beside it.
    """
    order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    ledger_rows = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.order_id == order_id)
                .order_by(LedgerEntry.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return {
        **_admin_order_out(order),
        "tariff_version": order.tariff_version,
        "fare": order.fare_json,
        "broadcast_radius_km": meter_str(Decimal(order.broadcast_radius_km)),
        "timeline": _order_timeline(order),
        "ledger": {"items": [_ledger_out(e) for e in ledger_rows]},
    }


# --------------------------------------------------------------------------- #
# disputes — the after-the-fact judgement about who bears a cost
# --------------------------------------------------------------------------- #


class DisputeCreateIn(BaseModel):
    """Open a case by hand.

    `severity` defaults to `NORMAL` and `sla_due_at` is derived from it by the
    service, never accepted from the client — a caller that could set its own
    deadline could quietly give a safety report a week.
    """

    category: str = Field(pattern=r"^(FARE|CONDUCT|SAFETY|LOST_ITEM|APP_ISSUE|OTHER)$")
    summary: str = Field(min_length=1, max_length=4000)
    order_id: uuid.UUID | None = None
    severity: str = Field(
        default=DisputeSeverity.NORMAL.value,
        pattern=r"^(LOW|NORMAL|HIGH|SAFETY_CRITICAL)$",
    )
    against_kind: str | None = Field(default=None, pattern=r"^(PASSENGER|DRIVER|PLATFORM)$")
    against_id: uuid.UUID | None = None


class DisputeMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    is_internal: bool = False


class DisputeResolveIn(BaseModel):
    """The decision. `note` is required even for `NONE` — see the service."""

    resolution: str = Field(
        pattern=(
            r"^(NONE|CHARGE_PASSENGER|CHARGE_DRIVER"
            r"|REFUND_PLATFORM_FEE|WAIVED_PLATFORM_FEE)$"
        )
    )
    note: str = Field(min_length=1, max_length=4000)
    close: bool = False


class DisputeStatusIn(BaseModel):
    status: str = Field(pattern=r"^(OPEN|INVESTIGATING|AWAITING_PARTY|ESCALATED|CLOSED)$")


def _dispute_out(dispute: OrderDispute, *, now: datetime | None = None) -> dict:
    """One queue row, with the derived fields the console sorts and colours by.

    `seconds_until_due` is computed server-side against the same clock the sort
    uses. The client recomputing it from `sla_due_at` and its own system time
    gives a different answer on a machine with a skewed clock, and the row then
    contradicts the order it is displayed in.
    """
    now = now or datetime.now(UTC)
    due = dispute.sla_due_at
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return {
        "id": str(dispute.id),
        "order_id": str(dispute.order_id) if dispute.order_id else None,
        "source": dispute.source,
        "category": dispute.category,
        "severity": dispute.severity,
        "status": dispute.status,
        "summary": dispute.summary,
        "raised_by_kind": dispute.raised_by_kind,
        "against_kind": dispute.against_kind,
        "assigned_admin_id": (
            str(dispute.assigned_admin_id) if dispute.assigned_admin_id else None
        ),
        "safety_flag": dispute.safety_flag,
        "sla_due_at": due.isoformat() if due else "",
        "sla_hours": dispute.severity_enum.sla_hours,
        "seconds_until_due": int((due - now).total_seconds()) if due else 0,
        "is_overdue": case_is_overdue(dispute, now=now),
        "resolution": dispute.resolution,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
        "created_at": dispute.created_at.isoformat(),
    }


def _dispute_message_out(message: DisputeMessage) -> dict:
    return {
        "id": message.id,
        "author_kind": message.author_kind,
        "author_id": str(message.author_id) if message.author_id else None,
        "author_label": message.author_label,
        "body": message.body,
        "is_internal": message.is_internal,
        "created_at": message.created_at.isoformat(),
    }


@router.get("/disputes", response_model=AdminDisputePageOut)
async def list_disputes(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    assigned_admin_id: uuid.UUID | None = Query(default=None),
    unassigned_only: bool = Query(default=False),
    open_only: bool = Query(default=True),
    overdue_only: bool = Query(default=False),
    order_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """The dispute queue. Any role may read it — only deciding is narrowed.

    **Sorted by SLA ascending (`sla_due_at`), not by recency.** Deliberate: an
    incident queue sorted newest-first answers whatever arrived while somebody
    was watching and buries the case about to breach. `open_only` defaults to
    true because the console's default view is the work remaining, not the
    archive.
    """
    for value, enum_type, field in (
        (status, DisputeStatus, "status"),
        (category, DisputeCategory, "category"),
        (severity, DisputeSeverity, "severity"),
    ):
        if value is None:
            continue
        try:
            enum_type(value)
        except ValueError as exc:
            raise BusinessRuleError(
                f"unknown {field}: {value!r}",
                {"reason": "INVALID_FILTER", "field": field, "value": value},
            ) from exc

    rows, total = await DisputeService(session_factory).list_cases(
        status=status,
        category=category,
        severity=severity,
        assigned_admin_id=assigned_admin_id,
        unassigned_only=unassigned_only,
        open_only=open_only,
        overdue_only=overdue_only,
        order_id=order_id,
        limit=limit,
        offset=offset,
    )
    now = datetime.now(UTC)
    return {
        "items": [_dispute_out(d, now=now) for d in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/disputes/stats", response_model=DisputeStatsOut)
async def dispute_stats(
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Queue header counts, computed server-side against one clock."""
    return await DisputeService(session_factory).stats()


@router.post("/disputes", status_code=201, response_model=AdminDisputeDetailOut)
async def create_dispute(
    payload: DisputeCreateIn,
    request: Request,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Open a case by hand. OPERATIONS or above.

    A manual case still gets an SLA from its severity, the same as an automatic
    one. The alternative — an operator-set deadline — is how a manually filed
    safety complaint ends up with less urgency than a machine-filed one.
    """
    session = session_factory()
    try:
        service = DisputeService(session_factory)
        dispute = await service.open_case(
            category=payload.category,
            summary=payload.summary,
            source=DisputeSource.ADMIN_CREATED,
            order_id=payload.order_id,
            raised_by_kind="ADMIN",
            raised_by_id=admin.id,
            against_kind=payload.against_kind,
            against_id=payload.against_id,
            severity=payload.severity,
        )
        await record_audit(
            session,
            event=EV_DISPUTE_CREATE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail=f"opened {payload.category} dispute",
            payload={
                "dispute_id": str(dispute.id),
                "category": payload.category,
                "severity": dispute.severity,
                "order_id": str(payload.order_id) if payload.order_id else None,
                "safety_flag": dispute.safety_flag,
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    full = await DisputeService(session_factory).get(dispute.id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.get("/disputes/{dispute_id}", response_model=AdminDisputeDetailOut)
async def dispute_detail(
    dispute_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """One case and its whole thread — internal notes included.

    Admin-only, so the full thread is correct here. A party-facing view must be
    a separate route with its own filter; a `?include_internal=` flag on this
    one is a filter somebody eventually forgets, and what it leaks is a staff
    opinion about a customer.
    """
    full = await DisputeService(session_factory).get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/assign", response_model=AdminDisputeDetailOut)
async def assign_dispute(
    dispute_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Claim a case, moving `OPEN` to `INVESTIGATING`."""
    service = DisputeService(session_factory)
    # The returned row is re-read below so the response carries the thread, so
    # only the previous assignee is needed here — it goes in the audit payload.
    _, previous = await service.assign(dispute_id, admin_id=admin.id)

    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_ASSIGN,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail="assigned dispute",
            payload={
                "dispute_id": str(dispute_id),
                "from": str(previous) if previous else None,
                "to": str(admin.id),
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    full = await service.get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/messages", status_code=201, response_model=DisputeMessageOut)
async def add_dispute_message(
    dispute_id: uuid.UUID,
    payload: DisputeMessageIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Add a turn to the thread.

    Any role may reply — SUPPORT's whole function is to be the first responder,
    and gating that would put the least experienced team member in front of a
    customer with no way to answer them.

    `is_internal` is recorded on the message and in the audit payload, because
    "who wrote this, and did they mean the customer to see it" is the question
    asked when a note is quoted back at us by mistake.
    """
    message = await DisputeService(session_factory).add_message(
        dispute_id,
        body=payload.body,
        author_kind="ADMIN",
        author_id=admin.id,
        author_label=None,
        is_internal=payload.is_internal,
    )

    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_MESSAGE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail="internal note" if payload.is_internal else "replied on dispute",
            # The body is NOT stored in the payload: it can be long, and it is
            # already the `dispute_messages` row. Duplicating free text into an
            # append-only table doubles the places a PII purge has to reach.
            payload={
                "dispute_id": str(dispute_id),
                "message_id": message.id,
                "is_internal": payload.is_internal,
                "length": len(payload.body),
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    return _dispute_message_out(message)


@router.post("/disputes/{dispute_id}/status", response_model=AdminDisputeDetailOut)
async def set_dispute_status(
    dispute_id: uuid.UUID,
    payload: DisputeStatusIn,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Move between non-terminal states (escalate, await the party, close).

    `RESOLVED` is refused here by the service: resolution carries a decision, a
    note and an actor, and a status flip would produce a case that reads as
    decided with none of them recorded.
    """
    service = DisputeService(session_factory)
    await service.set_status(dispute_id, status=payload.status)
    full = await service.get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeResolveOut)
async def resolve_dispute(
    dispute_id: uuid.UUID,
    payload: DisputeResolveIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Decide the money question.

    **The 403 is per-request, not on the route.** Judging conduct is OPERATIONS'
    job and moving money is FINANCE's, so this endpoint opens for both and then
    narrows: a resolution whose `moves_money` is true additionally requires
    FINANCE. A fixed `require_role(FINANCE)` on the route would lock OPERATIONS
    out of the `NONE` and `CHARGE_PASSENGER` decisions it is exactly the right
    role to make; a fixed `require_role(OPERATIONS)` would let the operator who
    judged the conduct also authorise the payout, which is the separation of
    duties the two roles exist to enforce.

    The check reads the **live** role via `live_admin_role`, not the token
    claim, so a demotion takes effect on the next request rather than at token
    expiry — the same authority `require_role` uses, for the same reason.
    """
    resolution = DisputeResolution(payload.resolution)
    session = session_factory()
    try:
        # OPERATIONS is the floor for deciding anything at all.
        actor_role = await live_admin_role(session, admin)
        if not actor_role.at_least(AdminRole.OPERATIONS):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "insufficient admin role",
                    "reason": "ADMIN_ROLE_INSUFFICIENT",
                    "required": AdminRole.OPERATIONS.value,
                    "actual": actor_role.value,
                },
            )
        if resolution.moves_money and not actor_role.at_least(AdminRole.FINANCE):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "this resolution moves money and requires FINANCE",
                    "reason": "DISPUTE_RESOLUTION_REQUIRES_FINANCE",
                    "resolution": resolution.value,
                    "required": AdminRole.FINANCE.value,
                    "actual": actor_role.value,
                },
            )
    finally:
        await session.close()

    service = DisputeService(session_factory)
    dispute = await service.resolve(
        dispute_id,
        resolution=resolution,
        note=payload.note,
        admin_id=admin.id,
        close=payload.close,
    )

    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_RESOLVE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail=f"resolved as {resolution.value}",
            payload={
                "dispute_id": str(dispute_id),
                "resolution": resolution.value,
                "moves_money": resolution.moves_money,
                "close": payload.close,
                # The note IS stored here, unlike a message body: it is the
                # decision's justification, and an audit row that says only
                # "resolved as CHARGE_DRIVER" cannot answer "why" a month later.
                "note": payload.note,
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    return {
        "id": str(dispute.id),
        "status": dispute.status,
        "resolution": dispute.resolution,
        "moves_money": resolution.moves_money,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else "",
    }

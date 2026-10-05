"""KYC queue, driver detail, and the two money moves against a driver.

`/drivers*`. Reads are open to every admin role; the KYC decision is OPERATIONS,
and the deposit grant/adjust are FINANCE (see `_roles.py`)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._roles import _require_finance, _require_operations
from app.api.admin._shared import _ledger_out, _refund_out
from app.api.schemas import (
    DepositAdjustOut,
    DepositGrantOut,
    DriverDetailOut,
    DriverPageOut,
    DriverReviewOut,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.money import money_str
from app.models import (
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    Fleet,
    FleetMembership,
    FleetMemberStatus,
    LedgerEntry,
    LedgerEntryType,
    RefundRequest,
)
from app.services.admin.audit_service import (
    EV_DEPOSIT_ADJUST,
    EV_DEPOSIT_GRANT,
    EV_KYC_DECISION,
    record_audit,
)
from app.services.ledger.ledger_service import (
    LedgerService,
    reference_for_adjustment,
    reference_for_grant,
)
from app.services.order.state_machine import assert_driver_transition

router = APIRouter()


class DriverReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject|suspend|terminate|restore)$")
    note: str = ""


_DECISION_TARGET = {
    "approve": DriverStatus.DEPOSIT_REQUIRED,
    "reject": DriverStatus.TERMINATED,
    "suspend": DriverStatus.SUSPENDED,
    "terminate": DriverStatus.TERMINATED,
    # A suspended driver is restored to ACTIVE, not run through the KYC door
    # again. `approve` maps to DEPOSIT_REQUIRED, which is why the old console
    # "Restore" button was an illegal transition waiting to fail.
    "restore": DriverStatus.ACTIVE,
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
    count_q = select(func.count()).select_from(DriverProfile)
    if status_filter:
        try:
            status = DriverStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "unknown driver status",
                    "reason": "UNKNOWN_STATUS",
                    "allowed": [s.value for s in DriverStatus],
                },
            ) from None
        q = q.where(DriverProfile.status == status)
        # The count carries the page's predicate. Counting the whole table while
        # returning a filtered page makes `total` describe a set the caller never
        # asked for, and the console sizes its pager from it.
        count_q = count_q.where(DriverProfile.status == status)
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
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
    required = (
        Decimal(dep.required_hkd)
        if dep is not None
        else Decimal(get_settings().driver_deposit_default_hkd)
    )
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

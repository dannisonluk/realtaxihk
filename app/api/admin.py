"""Admin API: KYC review queue + decisions + deposit grants. ADMIN enforced.

Hardening: require_admin now re-reads the live user row (P0-3). Deposit grants
are idempotent when the client supplies `reference` (P1-7): a retried grant
replays the original entry instead of double-crediting. Driver listing is
paginated (P1-8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
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
    RefundStatus,
)
from app.services.ledger_service import LedgerService, reference_for_grant
from app.services.refund_service import RefundService
from app.services.settlement_service import SettlementService
from app.services.state_machine import assert_driver_transition

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class DriverReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject|suspend|terminate)$")
    note: str = ""


_DECISION_TARGET = {
    "approve": DriverStatus.DEPOSIT_REQUIRED,
    "reject": DriverStatus.TERMINATED,
    "suspend": DriverStatus.SUSPENDED,
    "terminate": DriverStatus.TERMINATED,
}


@router.get("/drivers")
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
        "held_hkd": money_str(Decimal(dep.held_hkd)) if dep is not None else "0.0",
        "required_hkd": money_str(required),
        "is_fulfilled": dep.is_fulfilled if dep is not None else False,
        "has_account": dep is not None,
        "shortfall_hkd": money_str(max(required - balance, Decimal(0))),
    }


def _ledger_out(entry: LedgerEntry) -> dict:
    return {
        "id": entry.id,
        "entry_type": entry.entry_type.value,
        "amount_hkd": money_str(Decimal(entry.amount_hkd)),
        "balance_after_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "order_id": str(entry.order_id) if entry.order_id else None,
        "note": entry.note,
        "reference": entry.reference,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/drivers/{driver_id}")
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


@router.post("/drivers/{driver_id}/review")
async def review_driver(
    driver_id: str,
    payload: DriverReviewIn,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    dp = await session.get(DriverProfile, driver_id)
    if dp is None:
        raise HTTPException(status_code=404, detail="driver not found")
    target = _DECISION_TARGET[payload.decision]
    assert_driver_transition(dp.status, target)
    dp.status = target
    dp.kyc_reviewed_by = admin.id
    dp.kyc_reviewed_at = datetime.now(UTC)
    await session.flush()
    return {"id": str(dp.id), "status": dp.status.value}


class DepositGrantIn(BaseModel):
    amount_hkd: Decimal = Field(gt=0, le=100000)
    note: str = ""
    # P1-7 idempotency: client-supplied key; a retry with the same key replays.
    # SEC-13: it is namespaced server-side (see reference_for_grant) and never
    # used verbatim as the ledger reference.
    reference: str | None = Field(default=None, max_length=120)


@router.post("/drivers/{driver_id}/deposit/grant")
async def grant_deposit(
    driver_id: str,
    payload: DepositGrantIn,
    admin: Principal = Depends(require_admin),
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
    if deposit.is_fulfilled and dp.status == DriverStatus.DEPOSIT_REQUIRED:
        assert_driver_transition(dp.status, DriverStatus.ACTIVE)
        dp.status = DriverStatus.ACTIVE

    return {
        "id": str(dp.id),
        "driver_status": dp.status.value,
        "balance_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "is_fulfilled": deposit.is_fulfilled,
        "reference": entry.reference,
    }


@router.post("/settlement/weekly/run")
async def run_weekly_settlement(
    period: Annotated[str | None, Query(pattern=r"^\d{4}-W\d{2}$")] = None,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Run the weekly service-fee settlement by hand (idempotent per ISO week).

    The scheduled job fires once at boot then every 7 days; this is the ops lever
    to re-run a specific period (e.g. after a failed batch) without touching the
    database. Re-running an already-settled period charges nobody.
    """
    settings = get_settings()
    return await SettlementService(session_factory).run_weekly(settings.weekly_fee_hkd, period)


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


@router.get("/refunds")
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


@router.post("/refunds/{refund_id}/decision")
async def decide_refund(
    refund_id: uuid.UUID,
    payload: RefundDecisionIn,
    admin: Principal = Depends(require_admin),
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
    return _refund_out(refund)

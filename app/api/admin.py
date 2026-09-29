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
from app.models import DriverProfile, DriverStatus, LedgerEntryType, RefundRequest, RefundStatus
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

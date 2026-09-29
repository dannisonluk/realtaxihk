"""Admin API: KYC review queue + decisions + deposit grants. ADMIN enforced.

Hardening: require_admin now re-reads the live user row (P0-3). Deposit grants
are idempotent when the client supplies `reference` (P1-7): a retried grant
replays the original entry instead of double-crediting. Driver listing is
paginated (P1-8).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.money import money_str
from app.models import DriverProfile, DriverStatus, LedgerEntryType
from app.services.ledger_service import LedgerService
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
    rows = (
        (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    )
    total = (
        await session.execute(select(func.count()).select_from(DriverProfile))
    ).scalar_one()
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
    dp.kyc_reviewed_at = datetime.now(timezone.utc)
    await session.flush()
    return {"id": str(dp.id), "status": dp.status.value}


class DepositGrantIn(BaseModel):
    amount_hkd: Decimal = Field(gt=0, le=100000)
    note: str = ""
    # P1-7 idempotency: client-supplied key; a retry with the same key replays.
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

    reference = payload.reference or f"grant:{dp.id}:{uuid.uuid4().hex}"
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

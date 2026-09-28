"""Driver self-service API: registration (enters PENDING_KYC), profile view."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, get_current_user
from app.core.masking import mask_phone
from app.models import DriverDeposit, DriverProfile, DriverStatus, User, LedgerEntry
from sqlalchemy import select
from app.services.state_machine import assert_driver_transition

router = APIRouter(prefix="/api/v1/drivers", tags=["drivers"])


class DriverRegisterIn(BaseModel):
    hk_id_last4: str = Field(pattern=r"^\d{4}$")
    taxi_driver_plate_no: str = Field(min_length=4, max_length=10)
    vehicle_reg_mark: str = Field(min_length=4, max_length=8)
    taxi_type: str = Field(pattern=r"^(URBAN|NT|LANTAU)$")


def _deposit_out(dep: DriverDeposit | None) -> dict | None:
    if dep is None:
        return None
    return {
        "balance_hkd": str(Decimal(dep.balance_hkd).quantize(Decimal("0.1"))),
        "held_hkd": str(Decimal(dep.held_hkd).quantize(Decimal("0.1"))),
        "required_hkd": str(Decimal(dep.required_hkd).quantize(Decimal("0.1"))),
        "is_fulfilled": dep.is_fulfilled,
    }


def _profile_out(dp: DriverProfile) -> dict:
    return {
        "id": str(dp.id),
        "user_id": str(dp.user_id),
        "status": dp.status.value,
        "taxi_type": dp.taxi_type,
        "taxi_driver_plate_no": dp.taxi_driver_plate_no,
        "vehicle_reg_mark": dp.vehicle_reg_mark,
        "is_online": dp.is_online,
    }


async def _get_profile(session: AsyncSession, user_id) -> DriverProfile | None:
    return (
        await session.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
    ).scalars().first()


@router.post("/register", status_code=201)
async def register_driver(
    payload: DriverRegisterIn,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if await _get_profile(session, user.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="driver profile exists")
    profile = DriverProfile(
        user_id=user.id,
        hk_id_last4=payload.hk_id_last4,
        taxi_driver_plate_no=payload.taxi_driver_plate_no,
        vehicle_reg_mark=payload.vehicle_reg_mark,
        taxi_type=payload.taxi_type,
        status=DriverStatus.PENDING_KYC,
    )
    session.add(profile)
    await session.flush()
    return _profile_out(profile)


@router.get("/me")
async def my_driver_profile(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _get_profile(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    deposit = (
        await session.execute(
            select(DriverDeposit).where(DriverDeposit.driver_profile_id == profile.id)
        )
    ).scalars().first()
    out = _profile_out(profile)
    out["deposit"] = _deposit_out(deposit)
    if deposit is None:
        out["deposit"] = {"required_hkd": "500.0", "is_fulfilled": False}
    return out


@router.get("/me/ledger")
async def my_ledger(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _get_profile(session, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="no driver profile")
    rows = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.driver_profile_id == profile.id)
            .order_by(LedgerEntry.id.asc())
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "entry_type": r.entry_type.value,
                "amount_hkd": str(Decimal(r.amount_hkd).quantize(Decimal("0.1"))),
                "balance_after_hkd": str(
                    Decimal(r.balance_after_hkd).quantize(Decimal("0.1"))
                ),
                "order_id": str(r.order_id) if r.order_id else None,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }

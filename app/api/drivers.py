"""Driver self-service API: registration (enters PENDING_KYC), profile view.

SEC-12: every route here runs `require_active_user`. The module previously mixed
`get_current_user` (JWT signature only) with `require_active_user` (live DB
`is_active` check), so a disabled account kept reading its ledger and refund
state for the remaining lifetime of its access token.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    DriverProfileOut,
    DriverProfileWithDepositOut,
    LedgerPageOut,
    RefundRequestOut,
    RefundViewOut,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require_active_user, require_phone_current
from app.core.money import money_str
from app.models import DriverDeposit, DriverProfile, DriverStatus, LedgerEntry, RefundRequest
from app.services.ledger.refund_service import RefundService

router = APIRouter(prefix="/api/v1/drivers", tags=["drivers"])


class DriverRegisterIn(BaseModel):
    hk_id_last4: str = Field(pattern=r"^\d{4}$")
    taxi_driver_plate_no: str = Field(min_length=4, max_length=10)
    vehicle_reg_mark: str = Field(min_length=4, max_length=8)
    taxi_type: str = Field(pattern=r"^(URBAN|NT|LANTAU)$")


def _deposit_out(dep: DriverDeposit | None) -> dict | None:
    """The driver's own deposit figures, at the platform's canonical 2-dp wire form.

    These went through a local `quantize(Decimal("0.1"))` — the same rounding as
    `money_str`, but with `quantize`'s default `ROUND_HALF_EVEN`, so a tie
    (`150.45`) rounded *down* here and *up* in the admin view of the same row.
    Routing through `money_str` makes a driver's statement and an operator's
    view of it agree to the cent, and keeps the rounding mode in one place.
    """
    if dep is None:
        return None
    return {
        "balance_hkd": money_str(dep.balance_hkd),
        "held_hkd": money_str(dep.held_hkd),
        "required_hkd": money_str(dep.required_hkd),
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


@router.post("/register", status_code=201, response_model=DriverProfileOut)
async def register_driver(
    payload: DriverRegisterIn,
    user: Principal = Depends(require_phone_current),
    session: AsyncSession = Depends(get_session),
):
    if await DriverProfile.for_user(session, user.id) is not None:
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


@router.get("/me", response_model=DriverProfileWithDepositOut, response_model_exclude_unset=True)
async def my_driver_profile(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    deposit = (
        (
            await session.execute(
                select(DriverDeposit).where(DriverDeposit.driver_profile_id == profile.id)
            )
        )
        .scalars()
        .first()
    )
    out = _profile_out(profile)
    out["deposit"] = _deposit_out(deposit)
    if deposit is None:
        out["deposit"] = {
            "required_hkd": money_str(Decimal(get_settings().driver_deposit_default_hkd)),
            "is_fulfilled": False,
        }
    return out


@router.get("/me/ledger", response_model=LedgerPageOut)
async def my_ledger(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P1-8: keyset-paginated statement, ascending by id.

    A driver's ledger grows without bound — returning every row (the previous
    `.all()`) would eventually blow the response size. `after_id` is the `id` of
    the last row the client already holds; `next_cursor` stays non-null only
    while a full page came back.
    """
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="no driver profile")
    q = (
        select(LedgerEntry)
        .where(LedgerEntry.driver_profile_id == profile.id)
        .order_by(LedgerEntry.id.asc())
        .limit(limit)
    )
    if after_id is not None:
        q = q.where(LedgerEntry.id > after_id)
    rows = (await session.execute(q)).scalars().all()
    next_cursor = rows[-1].id if len(rows) == limit else None
    return {
        "items": [
            {
                "id": r.id,
                "entry_type": r.entry_type.value,
                "amount_hkd": money_str(r.amount_hkd),
                "balance_after_hkd": money_str(r.balance_after_hkd),
                "order_id": str(r.order_id) if r.order_id else None,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }


class RefundRequestIn(BaseModel):
    note: str = Field(default="", max_length=500)
    amount_hkd: Decimal | None = Field(default=None, gt=0, le=100000)


def _refund_out(r: RefundRequest) -> dict:
    return {
        "id": str(r.id),
        "amount_hkd": money_str(r.amount_hkd),
        "is_partial": r.is_partial,
        "status": r.status.value,
        "note": r.note,
        "decision_note": r.decision_note,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.post("/me/refund/request", status_code=201, response_model=RefundRequestOut)
async def request_refund(
    payload: RefundRequestIn,
    user: Principal = Depends(require_phone_current),
    session: AsyncSession = Depends(get_session),
):
    """Ask to withdraw part or all of the remaining deposit.

    The requested amount is *held*, not paid — an admin must approve before
    any money leaves the platform. Requesting suspends the driver, which stops
    dispatch and pauses the weekly service fee. At most one open request per
    driver. Omitting `amount_hkd` withdraws the whole balance (unchanged
    behaviour); passing an amount less than the balance is a partial
    withdrawal and on approval the driver returns to ACTIVE instead of being
    terminated.
    """
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    refund = await RefundService(session).request(
        profile,
        note=payload.note or None,
        amount_hkd=payload.amount_hkd,
        min_amount_hkd=get_settings().refund_min_hkd,
    )
    return _refund_out(refund)


@router.get("/me/refund", response_model=RefundViewOut)
async def my_refund(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """The driver's most recent refund request (null if they never filed one)."""
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    row = (
        (
            await session.execute(
                select(RefundRequest)
                .where(RefundRequest.driver_profile_id == profile.id)
                .order_by(RefundRequest.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return {"refund": _refund_out(row) if row is not None else None}

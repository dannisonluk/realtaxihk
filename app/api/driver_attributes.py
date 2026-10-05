"""Driver self-service attribute API: payment methods and in-car environment.

These routes write only the driver's own declared attributes. The passenger
reads them through the order/driver views, not through these endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    DriverEnvironmentOut,
    DriverPaymentMethodsOut,
)
from app.api.schemas.driver_attributes import InCarEnvironmentIn, PaymentMethodsIn
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.models import DriverPaymentMethod, DriverProfile, PaymentMethod

router = APIRouter(prefix="/api/v1/drivers/me", tags=["drivers"])


async def _require_profile(session: AsyncSession, user: Principal) -> DriverProfile:
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    return profile


@router.get("/payment-methods", response_model=DriverPaymentMethodsOut)
async def my_payment_methods(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    rows = (
        (
            await session.execute(
                select(DriverPaymentMethod.method).where(
                    DriverPaymentMethod.driver_profile_id == profile.id
                )
            )
        )
        .scalars()
        .all()
    )
    return {"methods": [str(m) for m in rows]}


@router.put("/payment-methods", response_model=DriverPaymentMethodsOut)
async def set_payment_methods(
    payload: PaymentMethodsIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    try:
        methods = [PaymentMethod(m) for m in payload.methods]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unknown payment method",
        ) from exc
    if not methods:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at least one payment method is required",
        )
    await session.execute(
        delete(DriverPaymentMethod).where(DriverPaymentMethod.driver_profile_id == profile.id)
    )
    for method in methods:
        session.add(DriverPaymentMethod(driver_profile_id=profile.id, method=method))
    await session.flush()
    return {"methods": [m.value for m in methods]}


@router.get("/environment", response_model=DriverEnvironmentOut)
async def my_environment(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    env = profile.in_car_environment_json or {}
    return {
        "silent_ride": bool(env.get("silent_ride", False)),
        "no_radio_music": bool(env.get("no_radio_music", False)),
        "no_smoke": bool(env.get("no_smoke", False)),
        "no_perfume": bool(env.get("no_perfume", False)),
    }


@router.put("/environment", response_model=DriverEnvironmentOut)
async def set_environment(
    payload: InCarEnvironmentIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    profile.in_car_environment_json = {
        "silent_ride": payload.silent_ride,
        "no_radio_music": payload.no_radio_music,
        "no_smoke": payload.no_smoke,
        "no_perfume": payload.no_perfume,
    }
    await session.flush()
    return {
        "silent_ride": payload.silent_ride,
        "no_radio_music": payload.no_radio_music,
        "no_smoke": payload.no_smoke,
        "no_perfume": payload.no_perfume,
    }

"""Pre-booking API: landmarks and driver booking preferences."""

from __future__ import annotations

import re
from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.prebooking import (
    DriverBookingPreferenceIn,
    DriverBookingPreferenceOut,
    LandmarkListOut,
    LandmarkOut,
)
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.models import (
    DriverBookingPreference,
    DriverProfile,
    DriverStatus,
    Landmark,
    LandmarkCategory,
)
from app.services.order.order_service import _point_lat, _point_lng

router = APIRouter(tags=["prebooking"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _landmark_out(landmark: Landmark) -> LandmarkOut:
    return LandmarkOut(
        id=str(landmark.id),
        code=landmark.code,
        name_en=landmark.name_en,
        name_zh=landmark.name_zh,
        category=landmark.category.value,
        lat=_point_lat(landmark.location) or 0.0,
        lng=_point_lng(landmark.location) or 0.0,
        radius_m=landmark.radius_m,
        sort_order=landmark.sort_order,
        is_active=landmark.is_active,
    )


def _parse_time(value: str | None) -> time | None:
    if value is None:
        return None
    if not _TIME_RE.match(value):
        raise HTTPException(status_code=422, detail={"reason": "INVALID_TIME"})
    hour, minute = (int(x) for x in value.split(":"))
    return time(hour, minute)


@router.get(
    "/api/v1/landmarks",
    response_model=LandmarkListOut,
)
async def list_landmarks(
    response: Response,
    category: LandmarkCategory | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_active_user),
) -> LandmarkListOut:
    query = select(Landmark).where(Landmark.is_active.is_(True))
    if category is not None:
        query = query.where(Landmark.category == category)
    query = query.order_by(Landmark.sort_order.asc(), Landmark.code.asc())
    rows = (await session.execute(query)).scalars().all()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return LandmarkListOut(items=[_landmark_out(row) for row in rows])


async def _require_active_driver(session: AsyncSession, principal: Principal) -> DriverProfile:
    profile = await DriverProfile.for_user(session, principal.id)
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail={"reason": "DRIVER_NOT_ACTIVE"})
    return profile


@router.get(
    "/api/v1/drivers/me/booking-preferences",
    response_model=DriverBookingPreferenceOut,
)
async def get_booking_preferences(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_active_user),
) -> DriverBookingPreferenceOut:
    profile = await _require_active_driver(session, principal)
    preference = (
        await session.execute(
            select(DriverBookingPreference).where(
                DriverBookingPreference.driver_profile_id == profile.id
            )
        )
    ).scalar_one_or_none()
    if preference is None:
        return DriverBookingPreferenceOut(
            categories=[],
            preferred_origin_area=None,
            available_from=None,
            available_until=None,
            updated_at=datetime.now(),
        )
    return DriverBookingPreferenceOut(
        categories=list(preference.categories or []),
        preferred_origin_area=preference.preferred_origin_area,
        available_from=(
            preference.available_from.strftime("%H:%M") if preference.available_from else None
        ),
        available_until=(
            preference.available_until.strftime("%H:%M") if preference.available_until else None
        ),
        updated_at=preference.updated_at,
    )


@router.put(
    "/api/v1/drivers/me/booking-preferences",
    response_model=DriverBookingPreferenceOut,
)
async def put_booking_preferences(
    payload: DriverBookingPreferenceIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_active_user),
) -> DriverBookingPreferenceOut:
    profile = await _require_active_driver(session, principal)
    from_time = _parse_time(payload.available_from)
    until_time = _parse_time(payload.available_until)
    if from_time is not None and until_time is not None and from_time >= until_time:
        raise HTTPException(status_code=422, detail={"reason": "INVALID_TIME_RANGE"})

    preference = (
        await session.execute(
            select(DriverBookingPreference).where(
                DriverBookingPreference.driver_profile_id == profile.id
            )
        )
    ).scalar_one_or_none()
    if preference is None:
        preference = DriverBookingPreference(
            driver_profile_id=profile.id,
            categories=[category.value for category in payload.categories],
            preferred_origin_area=payload.preferred_origin_area,
            available_from=from_time,
            available_until=until_time,
        )
        session.add(preference)
    else:
        preference.categories = [category.value for category in payload.categories]
        preference.preferred_origin_area = payload.preferred_origin_area
        preference.available_from = from_time
        preference.available_until = until_time
    await session.flush()
    return DriverBookingPreferenceOut(
        categories=list(preference.categories or []),
        preferred_origin_area=preference.preferred_origin_area,
        available_from=(
            preference.available_from.strftime("%H:%M") if preference.available_from else None
        ),
        available_until=(
            preference.available_until.strftime("%H:%M") if preference.available_until else None
        ),
        updated_at=preference.updated_at,
    )


__all__ = ["router"]

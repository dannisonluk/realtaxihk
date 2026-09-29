"""Driver location tracking: PostGIS upsert + online GEO index (Redis).

The driver app calls this every 3-5s while online; the endpoint is
idempotent per tick. WebSocket streaming lands with Module D.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, get_current_user
from app.models import DriverProfile, DriverStatus
from app.services.geo_service import GeoService

router = APIRouter(prefix="/api/v1/driver", tags=["driver"])


class LocationIn(BaseModel):
    lat: float = Field(ge=22.1, le=22.6)
    lng: float = Field(ge=113.8, le=114.5)
    online: bool = True


@router.post("/location")
async def upsert_location(
    payload: LocationIn,
    request: Request,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    profile = (
        (await session.execute(select(DriverProfile).where(DriverProfile.user_id == user.id)))
        .scalars()
        .first()
    )
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="only ACTIVE drivers can stream location")

    profile.current_location = f"POINT({payload.lng} {payload.lat})"
    profile.last_location_at = datetime.now(UTC)
    profile.is_online = payload.online
    await session.flush()

    geo = GeoService(request.app.state.redis_factory())
    if payload.online:
        await geo.index_driver(str(profile.id), payload.lat, payload.lng)
    else:
        await geo.remove_driver(str(profile.id))
    return {"ok": True}

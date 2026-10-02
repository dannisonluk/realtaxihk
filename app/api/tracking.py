"""Driver location tracking: PostGIS upsert.

The driver app calls this every 3-5s while online; the endpoint is idempotent
per tick. WebSocket streaming lands with Module D.

Security:
- SEC-12 the route runs `require_active_user`, so a disabled account stops
  streaming immediately instead of for the remainder of its token lifetime;
- SEC-15 it is rate-limited per driver — each call is a PostGIS UPDATE and was
  previously unbounded;
- SEC-25 the write-only `geo:drivers:online` Redis index is gone. Nothing ever
  read it, nothing ever expired it, and it grew without bound (37 stale members
  in a live check). Online state already lives on `driver_profiles.is_online`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import OkOut
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.core.service_area import require_in_hong_kong
from app.models import DriverProfile, DriverStatus

# The driver surface is a plural collection (`/api/v1/drivers`), matching
# `drivers.py` and `licence.py` (`/api/v1/drivers/licence`). This module used to
# mount `/api/v1/driver` — the only singular collection in the API, and the only
# route whose path could not be guessed from the other two.
router = APIRouter(prefix="/api/v1/drivers", tags=["drivers"])


class LocationIn(BaseModel):
    lat: float = Field(ge=22.1, le=22.6)
    lng: float = Field(ge=113.8, le=114.5)
    online: bool = True


@router.post("/location", response_model=OkOut)
async def upsert_location(
    payload: LocationIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"driver:location:{user.id}",
        settings.driver_location_rate_limit,
        settings.driver_location_window_s,
    ):
        raise HTTPException(status_code=429, detail="location updates are too frequent")

    # Service area: a driver outside Hong Kong must not publish a position. The
    # field bounds above are a box that admits Shenzhen — see
    # `app/core/hk_bounds.py`. Refusing here also protects the dispatch index,
    # which is what the coordinate feeds.
    require_in_hong_kong(payload.lat, payload.lng, field="location")

    profile = await DriverProfile.for_user(session, user.id)
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="only ACTIVE drivers can stream location")

    profile.current_location = f"POINT({payload.lng} {payload.lat})"
    profile.last_location_at = datetime.now(UTC)
    profile.is_online = payload.online
    await session.flush()
    return {"ok": True}

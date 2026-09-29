"""Module D — REST trip snapshot: reconnection fallback for the live map.

GET /api/v1/trips/{order_id}/location — participants only (passenger owner
or assigned driver). Reads the driver's latest persisted PostGIS point via
ST_AsText (raw SQL, avoids Geography deserialization quirks).

Security:
- SEC-12 the route runs `require_active_user` (was JWT-signature only);
- SEC-27 the response identifies the driver by *profile* id. It used to return
  the driver's account UUID — the `sub` claim of their JWTs — which is an
  unnecessary identifier to hand to a passenger.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_active_user

router = APIRouter(prefix="/api/v1/trips", tags=["trips"])


@router.get("/{order_id}/location")
async def trip_location(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        oid = uuid.UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc

    row = (
        await session.execute(
            text(
                """
                SELECT
                  o.passenger_id,
                  o.driver_id,
                  o.status,
                  d.user_id                     AS driver_user_id,
                  ST_AsText(d.current_location) AS driver_wkt
                FROM orders o
                LEFT JOIN driver_profiles d ON d.id = o.driver_id
                WHERE o.id = :oid
                """
            ),
            {"oid": oid},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="order not found")
    passenger_id, driver_profile_id, status, driver_user_id, wkt = row

    # Party check uses the driver's account id, but that id never leaves the
    # server — only the profile id is returned below.
    is_participant = passenger_id == user.id or (
        driver_user_id is not None and driver_user_id == user.id
    )
    if not is_participant:
        raise HTTPException(status_code=403, detail="not a participant of this trip")

    lat = lng = None
    if wkt:
        lng_s, lat_s = wkt[6:-1].split()
        lng, lat = float(lng_s), float(lat_s)
    status_val = status.value if hasattr(status, "value") else str(status)
    return {
        "order_id": str(oid),
        "status": status_val,
        "driver_profile_id": str(driver_profile_id) if driver_profile_id is not None else None,
        "lat": lat,
        "lng": lng,
    }

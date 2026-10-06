"""Live map — where the fleet is right now. `/live/drivers`. Any admin role.

Polled, not pushed. `TripHub` exists but lifecycle events are never published, so
there is nothing to subscribe to; see `docs/DEVELOPMENT.md` §3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AdminLiveDriversOut
from app.core.db import get_session
from app.core.deps import Principal, require_admin

router = APIRouter()

_ACTIVE_ORDER_STATUSES = (
    "ACCEPTED",
    "PENDING_ARRIVAL_CONFIRM",
    "DRIVER_ARRIVED",
    "IN_TRIP",
    "DESTINATION_CHANGED",
)


_LIVE_DRIVERS_SQL = text(
    """
    SELECT
      d.id                          AS driver_profile_id,
      d.status                      AS driver_status,
      d.taxi_type                   AS taxi_type,
      d.vehicle_reg_mark            AS vehicle_reg_mark,
      d.is_online                   AS is_online,
      d.last_location_at            AS last_location_at,
      ST_AsText(d.current_location) AS wkt,
      a.order_id                    AS order_id,
      a.order_status                AS order_status
    FROM driver_profiles d
    LEFT JOIN LATERAL (
      SELECT o.id AS order_id, o.status AS order_status
      FROM orders o
      WHERE o.driver_id = d.id
        AND o.status IN :active_statuses
      ORDER BY o.created_at DESC
      LIMIT 1
    ) a ON TRUE
    WHERE d.current_location IS NOT NULL
      AND (CAST(:include_offline AS boolean) OR d.is_online)
    ORDER BY d.last_location_at DESC NULLS LAST
    LIMIT :row_limit
    """
).bindparams(bindparam("active_statuses", expanding=True))


@router.get("/live/drivers", response_model=AdminLiveDriversOut)
async def live_drivers(
    include_offline: bool = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every driver with a persisted position, and the order they are running.

    **Polled, not pushed.** A handful of operators watching a map does not
    justify a second WebSocket fan-out channel beside `/ws/trip/{order_id}`.
    Polling an indexed read every 5-10 s is cheaper, needs no new background
    task, and has no new failure mode. Sub-second freshness is the moment to
    add a broadcast channel — not before.

    The path is `/live/drivers`, not `/drivers/live`, because `/drivers/{id}`
    is declared above: a literal segment in that position would be captured as
    a driver id and every call would 404 with a confusing "invalid UUID".

    **Not audited per poll**, for the same reason the subject search is not —
    the console polls this every few seconds and the volume would bury the money
    events the log exists to surface.

    `require_admin` and no more: reading where the fleet is changes nothing and
    moves no money, and the roster endpoint under the same gate already exposes
    the plate.
    """
    # One row past the limit is fetched to answer `truncated` honestly:
    # `len(rows) == limit` cannot distinguish "exactly this many" from "more,
    # cut off", and a map that silently drops the 501st car looks like a fleet
    # that shrank.
    rows = (
        await session.execute(
            _LIVE_DRIVERS_SQL,
            {
                "active_statuses": list(_ACTIVE_ORDER_STATUSES),
                "include_offline": include_offline,
                "row_limit": limit + 1,
            },
        )
    ).all()

    truncated = len(rows) > limit

    drivers = []
    for row in rows[:limit]:
        # `ST_AsText` yields "POINT(lng lat)" — longitude first, which is the
        # opposite of how every other layer here speaks. The WHERE clause
        # guarantees a value, so there is no None branch to take.
        lng_str, lat_str = row.wkt[6:-1].split()
        drivers.append(
            {
                "driver_profile_id": str(row.driver_profile_id),
                "status": row.driver_status,
                "taxi_type": row.taxi_type,
                "vehicle_reg_mark": row.vehicle_reg_mark,
                "is_online": row.is_online,
                "last_location_at": (
                    row.last_location_at.isoformat() if row.last_location_at else None
                ),
                "lat": float(lat_str),
                "lng": float(lng_str),
                "order_id": str(row.order_id) if row.order_id is not None else None,
                "order_status": row.order_status,
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "drivers": drivers,
        "truncated": truncated,
    }

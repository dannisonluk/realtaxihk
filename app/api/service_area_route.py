"""Service-area check, for the client to ask before it acts.

The server enforces the boundary on every coordinate write regardless of what
this endpoint says — see `app/core/service_area.py`. This exists so the app can
find out *before* it starts a flow, and explain itself, rather than letting a
user fill in a whole booking form and then meet a 403.

Deliberately a `GET` with the coordinate in the query string: it has no side
effects, so it should be cacheable and safe to retry, and the client is likely
to call it on a timer as the device moves.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import Principal, require_active_user
from app.core.hk_bounds import HK_BBOX, is_in_hong_kong
from app.core.service_area import REASON_OUTSIDE_HK

router = APIRouter(prefix="/api/v1/service-area", tags=["service-area"])


@router.get("/check")
async def check_service_area(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    user: Principal = Depends(require_active_user),
) -> dict:
    """Whether this coordinate may use the platform.

    Returns `{"allowed": false, "reason": "OUTSIDE_HK"}` rather than a 403.

    The asymmetry with the write paths is intentional. A write from outside the
    area is a refusal and must look like one. This endpoint's *question* — "am I
    in the area?" — has a perfectly valid answer of "no", and answering it with
    an HTTP error would force every client to treat an expected outcome as an
    exception, which is how a boundary check ends up in a crash log instead of
    on a screen.

    The predicate is called directly rather than the guard being caught. Catching
    the guard's exception to mean "outside" would also swallow any *other* reason
    it might raise, and silently report a bug as a boundary decision.
    """
    if is_in_hong_kong(lat, lng):
        return {"allowed": True, "reason": None, "lat": lat, "lng": lng}
    return {
        "allowed": False,
        "reason": REASON_OUTSIDE_HK,
        "message": "This service is only available within Hong Kong.",
        "lat": lat,
        "lng": lng,
    }


@router.get("/bounds")
async def service_area_bounds(
    user: Principal = Depends(require_active_user),
) -> dict:
    """The outer bounding box, for a client that wants to short-circuit.

    Only the box, not the polygons: it is a superset, so a client using it can
    skip the call when the point is clearly outside, but must still ask when the
    point is inside. Shipping the polygon set would duplicate the boundary in
    two codebases, and the two copies would drift.
    """
    (lat_lo, lat_hi), (lng_lo, lng_hi) = HK_BBOX
    return {"lat_min": lat_lo, "lat_max": lat_hi, "lng_min": lng_lo, "lng_max": lng_hi}

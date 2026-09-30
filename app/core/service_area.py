"""The service-area gate: only Hong Kong may use this platform.

Enforcement lives here rather than in the client, and that is the whole point.
A mobile app can check the device's location and show a polite screen, but the
check it performs is advisory: the API is reachable directly, so anything the
client decides can simply be skipped. This module is the layer that cannot be
skipped, and the app's screen is a courtesy on top of it.

Where it is applied — the coordinate *write* paths, and nothing else
-------------------------------------------------------------------
  * `POST /orders` — pickup and dropoff
  * `PATCH`/driver location updates — the driver's own position
  * the WebSocket location tick

Reads are deliberately not gated. A driver who crosses into Shenzhen mid-shift
should still be able to see their current trip, their statement and their
profile; they simply cannot start or continue publishing new positions from
outside. Gating reads would turn a boundary rule into a lockout, and would also
break the case the boundary rule is least sure about — a device whose location
is momentarily wrong.

The refusal carries `reason: OUTSIDE_HK` and the offending coordinate, so a
client can distinguish "you are outside Hong Kong" from every other 403 and
explain itself, instead of surfacing a bare "forbidden".
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.hk_bounds import is_in_hong_kong

# Machine-readable reason. Stable — clients switch on it.
REASON_OUTSIDE_HK = "OUTSIDE_HK"


def require_in_hong_kong(lat: float, lng: float, *, field: str) -> None:
    """Raise 403 when the coordinate is outside Hong Kong territory.

    `field` names the offending input (`pickup`, `dropoff`, `location`) so the
    client can point at the right control rather than guessing.

    The coordinate is echoed back in `details`. That is a deliberate exception
    to the usual rule against reflecting caller input: the caller just sent it,
    so there is nothing to leak, and without it a client cannot tell which of
    two coordinates in the same request was rejected.
    """
    if is_in_hong_kong(lat, lng):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "reason": REASON_OUTSIDE_HK,
            "message": "This service is only available within Hong Kong.",
            "field": field,
            "lat": lat,
            "lng": lng,
        },
    )

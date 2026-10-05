"""HK region helpers and geofence matching for premium destinations.

The destination area is a coarse, server-derived label (HK_ISLAND / KOWLOON /
NT / AIRPORT / LANTAU). It deliberately mirrors the map polygons in
`app.core.hk_bounds` but is a separate concept: that module answers "is this
in Hong Kong", this module answers "which region is this". The values are
stable wire codes that the mobile client renders as localised labels.

Premium destination matching is a simple centroid-radius test. The platform is
an information intermediary, so this is a suggestion stored on the order, not
a contract: the driver can still see the full route and decide.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.hk_bounds import HK_BBOX

if TYPE_CHECKING:
    from app.models import PremiumDestination

# Stable wire codes. Keep them short and enum-like; clients match on these,
# not on localised names.
AREA_HK_ISLAND = "HK_ISLAND"
AREA_KOWLOON = "KOWLOON"
AREA_NT = "NT"
AREA_AIRPORT = "AIRPORT"
AREA_LANTAU = "LANTAU"

# The closed set, in one place, because it is now a **filter** input and not
# just an output. `destination_area()` can only ever return one of these (or
# None), so a caller passing anything else is not asking a narrower question —
# it is asking a question with no answer, and must be rejected rather than
# silently matched against nothing. A filter that quietly returns an empty page
# for a typo is worse than a 422: the driver reads it as "no orders nearby".
ALL_AREAS: frozenset[str] = frozenset(
    {AREA_HK_ISLAND, AREA_KOWLOON, AREA_NT, AREA_AIRPORT, AREA_LANTAU}
)


def is_valid_area(code: str) -> bool:
    """True when `code` is one of the stable wire codes above."""
    return code in ALL_AREAS

# Airport points for the coarse area test (Airport Express / T1/T2 / SkyCity).
_AIRPORT_POINTS = ((22.308, 113.9185), (22.315, 113.935), (22.321, 113.944))
_AIRPORT_RADIUS_KM = 2.0

# Lantau approximate centroid (excluding the airport strip, which is handled
# above). Coarse by design.
_LANTAU_POINTS = ((22.255, 113.95), (22.20, 113.98), (22.27, 114.02))
_LANTAU_RADIUS_KM = 10.0

_NT_BBOX = ((22.23, 22.6), (113.8, 114.45))


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres (WGS84)."""
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _near_any(
    lat: float,
    lng: float,
    points: tuple[tuple[float, float], ...],
    radius_km: float,
) -> bool:
    return any(_haversine_km(lat, lng, p[0], p[1]) <= radius_km for p in points)


def destination_area(lat: float, lng: float) -> str | None:
    """Return the coarse destination area for a coordinate, or None outside HK.

    Ordering matters: the airport and Lantau are subsets of the NT bbox, so
    they are checked before the generic NT path. Kowloon and Hong Kong Island
    are separated by the harbour; the bboxes are generous enough that a water
    point near the middle resolves to one of them, which is acceptable for a
    filter label.
    """
    (lat_lo, lat_hi), (lng_lo, lng_hi) = HK_BBOX
    if not (lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi):
        return None
    if _near_any(lat, lng, _AIRPORT_POINTS, _AIRPORT_RADIUS_KM):
        return AREA_AIRPORT
    if _near_any(lat, lng, _LANTAU_POINTS, _LANTAU_RADIUS_KM):
        return AREA_LANTAU
    if lat < 22.27:
        return AREA_HK_ISLAND
    if 113.85 <= lng <= 114.30 and 22.27 <= lat < 22.45:
        return AREA_KOWLOON
    if _NT_BBOX[0][0] <= lat <= _NT_BBOX[0][1] and _NT_BBOX[1][0] <= lng <= _NT_BBOX[1][1]:
        return AREA_NT
    return None


def destination_within_m(
    lat: float,
    lng: float,
    centre_lat: float,
    centre_lng: float,
    radius_m: int,
) -> bool:
    """True when a point is inside a destination's geofence."""
    return _haversine_km(lat, lng, centre_lat, centre_lng) * 1000 <= radius_m


@dataclass(frozen=True)
class PremiumMatch:
    id: str
    code: str
    name_zh: str
    name_en: str
    avatar_key: str | None
    radius_m: int


def match_premium_destination(
    lat: float,
    lng: float,
    destinations: Sequence[PremiumDestination],
) -> PremiumMatch | None:
    """Return the first ACTIVE premium destination whose geofence contains lat/lng.

    Callers pass the already-loaded ACTIVE rows; this module stays free of the
    session/ORM so tests can exercise it without a database.
    """
    for d in destinations:
        if str(d.status) != "ACTIVE":
            continue
        if destination_within_m(lat, lng, float(d.lat), float(d.lng), d.radius_m):
            return PremiumMatch(
                id=str(d.id),
                code=d.code,
                name_zh=d.name_zh,
                name_en=d.name_en,
                avatar_key=d.avatar_key,
                radius_m=d.radius_m,
            )
    return None

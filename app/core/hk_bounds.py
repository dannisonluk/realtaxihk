"""Is this coordinate in Hong Kong?

Why a polygon and not a bounding box
------------------------------------
The obvious implementation — and the one this module replaces — is a lat/lng
box. It is also wrong in the way that matters most: the box the API used to
validate against, `lat 22.1-22.6, lng 113.8-114.5`, **contains Shenzhen**.
Verified against those exact numbers:

    Shenzhen Futian   22.5410, 114.0540   INSIDE
    Shenzhen Luohu    22.5480, 114.1230   INSIDE
    Shenzhen Bao'an   22.5550, 113.8830   INSIDE

Shenzhen sits directly north of the New Territories across the Shenzhen River,
so any box wide enough to hold the New Territories also holds its northern
neighbour. For input validation that was tolerable — a bad coordinate produced
a bad order. As the gate for "only Hong Kong may use this app" it is fatal: the
users the check exists to stop would be the ones it admits.

So the boundary is a real polygon, and the box survives only as a cheap
reject: `HK_BBOX` is a superset of every polygon here, and a point outside it
cannot be inside any of them. Most rejected calls (a phone in London, a bug
sending 0,0) fail on four comparisons and never reach the ray cast.

What is included, and what that costs
-------------------------------------
Eight polygons: the main territory (New Territories, Kowloon and Hong Kong
Island, with Victoria Harbour inside it — a point in the harbour is Hong Kong,
and no operator is standing in it), Lantau including Chek Lap Kok so the
airport counts, and the populated islands a genuine user could be on.

The polygons are deliberately **coarse**. This answers "is this device in Hong
Kong", not "which district is this" — a boundary accurate to a few hundred
metres is well inside the noise of consumer GPS, and a tighter one would only
add vertices to maintain. The one place the coarse boundary must not be slack
is the northern land border, because that is where the check is load-bearing;
it follows the Shenzhen River corridor.

Measured against the river course, the northern edge admits at most ~610 m
beyond the true border, at Lo Wu and Man Kam To. That slack is deliberate and
is not a defect to be tightened away. Those longitudes are exactly where the
Hong Kong-side border crossings sit — Lok Ma Chau, Lo Wu, Man Kam To, Sha Tau
Kok — so pulling the line south to remove the slack would start refusing the
genuine Hong Kong users who work at them, which is the worse error. The slack
covers the river and the Frontier Closed Area; it does not reach Shenzhen's
built-up area, which begins further north. Verified: Futian (22.5410, 114.0540),
Luohu (22.5480, 114.1230), Nanshan (22.5330, 113.9300), Civic Center
(22.5450, 114.0550) and Huaqiangbei (22.5470, 114.0860) are all refused.

Being coarse means some genuinely Hong Kong water is outside the polygons —
the far south of the territory, and the sea between the islands. A boat there
would be refused. That is the deliberate trade: false negatives on open water
are cheap and rare, false positives in Shenzhen are neither.

The one place the boundary deliberately reaches **north of the Shenzhen River
corridor is the Shenzhen Bay Port Hong Kong Port Area**, which is Shenzhen
territory under Hong Kong jurisdiction — the HKSAR leases it from Shenzhen
until 2047-06-30 and applies Hong Kong law inside it. Its public transport
interchange is where a taxi bound for Shenzhen Bay Port actually drops off, so
a coordinate there must be in Hong Kong. See the Deep Bay block in `_HK_MAIN`.

Coordinates are WGS84, matching the SRID 4326 geography columns in the schema.
"""

from __future__ import annotations

from typing import NamedTuple


class LatLng(NamedTuple):
    lat: float
    lng: float


# Cheap reject. A superset of every polygon below, so a point outside it cannot
# be in Hong Kong. Kept slightly wider than the outermost vertex on purpose —
# if it were tight, a rounding difference could reject a real point, and the
# whole point of this box is that it never says no to a genuine Hong Kong
# coordinate.
HK_BBOX = ((22.13, 22.60), (113.80, 114.45))

# Main territory: New Territories + Kowloon + Hong Kong Island, with the
# harbour enclosed. Traced clockwise from the north-west corner.
#
# The northern edge follows the Shenzhen River corridor. This is the segment
# that does the work, so it is the most carefully placed: it passes south of
# Lok Ma Chau, Lo Wu, Man Kam To and Sha Tau Kok, putting each town on the
# Hong Kong side, and stays north of nothing that belongs to Shenzhen.
#
# The one place the northern edge is *deliberately* pushed north of the
# river corridor is the **Shenzhen Bay Port Hong Kong Port Area** — see the
# Deep Bay block below. Read that before moving any of the first few vertices.
#
# One caveat about the two `Sha Tau Kok` / `Ta Kwu Ling` vertices below, which
# only a test would notice: a point exactly *on* a vertex is a boundary case for
# the ray cast, and `is_in_hong_kong(22.5500, 114.1800)` returns False (the
# half-open `(yi > y) != (yj > y)` test counts a vertex once depending on
# traversal direction). The *towns* are unaffected — Sha Tau Kok proper
# (≈114.205–114.215) and Ta Kwu Ling both test True, and so does the coordinate
# the test suite uses. A user standing in the village is accepted; only the
# literal polygon corner is not, and no device reports that coordinate to the
# metre. Left as-is rather than nudging the vertices, because `_HK_MAIN` is
# hand-traced against the river and the coarse boundary is documented as
# accurate to a few hundred metres anyway.
_HK_MAIN: tuple[LatLng, ...] = (
    # -- northern land boundary (Deep Bay, then the Shenzhen River), W to E --
    #
    # Deep Bay detour: the Shenzhen Bay Port Hong Kong Port Area.
    #
    # Everything else on this edge follows the land border faithfully. This
    # stretch is the exception, and it is not a fudge — it is the correct
    # answer to a genuine question: the Hong Kong Port Area of Shenzhen Bay
    # Port is *inside Shenzhen's territory* but is **Hong Kong jurisdiction**.
    #
    # It is Hong Kong the way an embassy is the soil of its own country. The
    # National People's Congress Standing Committee authorised the HKSAR on
    # 2006-10-31 to exercise full Hong Kong law inside a sealed area of the
    # port; the State Council fixed its extent by 國函〔2006〕132號. The HKSAR
    # does not own the land — the Shenzhen government does — and leases it,
    # paying rent annually, **until 2047-06-30**. The port's public transport
    # interchange (深圳灣口岸公共運輸交匯處) is, by its own description, "香港
    # 管轄範圍內唯一並非位處香港土地的車站": the only Hong Kong-run bus
    # terminus that is not on Hong Kong soil.
    #
    # So "is this coordinate in Hong Kong" must be **True** for the Port Area.
    # A device there belongs to a user entitled to use this app — a driver
    # dropping off at the PTI, or a passenger being dropped there. Refusing
    # them is a false negative with a name: a taxi bound for Shenzhen Bay Port
    # was being judged to have left Hong Kong 1.34 km before arriving, because
    # a two-vertex Deep Bay edge cut the corner at a slope of 1.125 and left
    # the Port Area outside.
    #
    # The vertices below carve out **only** the Port Area and its immediate
    # approach, and nothing on the Shenzhen side. The west wall is pitched so
    # the PTI has roughly 450 m of margin — enough that GPS noise, or the
    # extent of the interchange itself, cannot put a genuine drop-off outside.
    # Verified against the negative controls that matter: Shekou's east shore
    # (22.501, 113.936), the Shenzhen Bay checkpoint on the Shenzhen side
    # (22.521, 113.941), Nanshan, Qianhai and Deep Bay's northern water are all
    # still refused. **The Port Area is admitted; a strip of Deep Bay water is
    # admitted with it; Shenzhen land is not.** Any future edit here must keep
    # that true — `tests/test_hk_bounds.py` asserts the whole set.
    LatLng(22.4600, 113.9200),  # north-west: Deep Bay / Ngau Hom Shek
    LatLng(22.4830, 113.9330),  # Deep Bay, climbing toward the Port Area
    LatLng(22.4880, 113.9380),  # Shenzhen Bay Bridge, HK side
    LatLng(22.4940, 113.9400),  # bridge approach
    LatLng(22.5010, 113.9412),  # Port Area: west wall, ~450 m west of the PTI
    LatLng(22.5055, 113.9440),  # Port Area: north-east, past the PTI
    LatLng(22.5070, 113.9500),  # Port Area: north wall, back toward the river
    LatLng(22.5050, 113.9600),  # Deep Bay, east side (unchanged)
    LatLng(22.5150, 114.0200),  # Shenzhen River mouth
    LatLng(22.5200, 114.0700),  # Lok Ma Chau (station sits just south of here)
    LatLng(22.5320, 114.1140),  # Lo Wu
    LatLng(22.5350, 114.1400),  # Man Kam To
    LatLng(22.5500, 114.1800),  # Ta Kwu Ling
    LatLng(22.5450, 114.2200),  # Sha Tau Kok
    LatLng(22.5300, 114.2500),  # Starling Inlet
    # -- east coast, southbound --
    LatLng(22.4700, 114.3300),  # Tap Mun approaches
    LatLng(22.4200, 114.3700),  # Sai Kung north-east
    LatLng(22.3800, 114.3400),  # Sai Kung
    LatLng(22.3200, 114.3100),  # Clear Water Bay
    LatLng(22.2700, 114.2800),  # Tung Lung Chau
    LatLng(22.2400, 114.2600),  # Hong Kong Island, Cape Collinson
    # -- Hong Kong Island south shore, westbound --
    LatLng(22.2100, 114.2150),  # Stanley
    LatLng(22.2200, 114.1600),  # Repulse Bay / Aberdeen
    LatLng(22.2450, 114.1100),  # Pok Fu Lam
    LatLng(22.2700, 114.1050),  # Mount Davis
    # -- Victoria Harbour west, northbound --
    LatLng(22.3200, 114.0900),  # Stonecutters Island
    LatLng(22.3600, 114.0700),  # Kwai Chung
    LatLng(22.3900, 114.0300),  # Ting Kau
    LatLng(22.3700, 113.9500),  # Tuen Mun
    LatLng(22.4000, 113.9200),  # Lung Kwu Tan
)

# Lantau, with Chek Lap Kok (the airport) inside the northern edge so a device
# at the airport is in Hong Kong — which it obviously is, and which a naive
# Lantau outline that followed the island's natural coast would get wrong,
# because the airport sits on reclaimed land north of the original shoreline.
_LANTAU: tuple[LatLng, ...] = (
    LatLng(22.2200, 113.8300),  # Tai O, south-west
    LatLng(22.2700, 113.8500),  # west
    LatLng(22.3100, 113.8900),  # north-west
    LatLng(22.3350, 113.9100),  # Chek Lap Kok, north shore
    LatLng(22.3350, 113.9700),  # airport, north-east
    LatLng(22.3450, 114.0100),  # Sunny Bay (reclaimed, north of the island)
    LatLng(22.3200, 114.0550),  # Discovery Bay
    LatLng(22.2300, 114.0500),  # Mui Wo, south-east
    LatLng(22.1950, 114.0000),  # south coast
    LatLng(22.1900, 113.9200),  # south
)

# Kat O (Crooked Island), off the north-east coast. Its own polygon rather than
# an extension of the main one: reaching it from the main outline would push the
# northern boundary up to around 22.56, and Yantian — Shenzhen's deep-water port
# — sits at 22.558, 114.24. The two are only ~0.02 deg apart, so a shared
# boundary risks admitting the port to include the island.
_KAT_O: tuple[LatLng, ...] = (
    LatLng(22.5300, 114.2700),
    LatLng(22.5550, 114.2750),
    LatLng(22.5550, 114.3100),
    LatLng(22.5300, 114.3050),
)

# Populated islands. Small, but a user standing on one is a real user, and
# refusing them would be a false negative with a visible cause.
_CHEUNG_CHAU: tuple[LatLng, ...] = (
    LatLng(22.1950, 114.0150),
    LatLng(22.2250, 114.0180),
    LatLng(22.2280, 114.0420),
    LatLng(22.1980, 114.0430),
)

_LAMMA: tuple[LatLng, ...] = (
    LatLng(22.1850, 114.0950),
    LatLng(22.2350, 114.1000),
    LatLng(22.2300, 114.1400),
    LatLng(22.1800, 114.1300),
)

_PENG_CHAU: tuple[LatLng, ...] = (
    LatLng(22.2780, 114.0280),
    LatLng(22.2950, 114.0300),
    LatLng(22.2960, 114.0480),
    LatLng(22.2790, 114.0470),
)

# Tung Ping Chau — the far north-east island, close to the mainland border.
_PING_CHAU: tuple[LatLng, ...] = (
    LatLng(22.5350, 114.4150),
    LatLng(22.5500, 114.4200),
    LatLng(22.5480, 114.4450),
    LatLng(22.5330, 114.4400),
)

# Ma Wan, between Lantau and the New Territories.
_MA_WAN: tuple[LatLng, ...] = (
    LatLng(22.3400, 114.0520),
    LatLng(22.3600, 114.0560),
    LatLng(22.3620, 114.0720),
    LatLng(22.3420, 114.0680),
)

HK_POLYGONS: tuple[tuple[LatLng, ...], ...] = (
    _HK_MAIN,
    _LANTAU,
    _CHEUNG_CHAU,
    _LAMMA,
    _PENG_CHAU,
    _PING_CHAU,
    _MA_WAN,
    _KAT_O,
)


def _in_polygon(lat: float, lng: float, polygon: tuple[LatLng, ...]) -> bool:
    """Ray casting: count crossings of a ray heading east from the point.

    An odd count means inside. The standard `(yi > y) != (yj > y)` form is used
    so a vertex exactly on the ray is counted once rather than twice — the
    half-open comparison `>` is what makes that work, and changing it to `>=`
    would double-count the vertices and flip the answer for points on the
    boundary.
    """
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i].lat, polygon[i].lng
        yj, xj = polygon[j].lat, polygon[j].lng
        if (yi > lat) != (yj > lat):
            # Longitude where edge i-j crosses this latitude.
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_cross:
                inside = not inside
        j = i
    return inside


def is_in_hong_kong(lat: float, lng: float) -> bool:
    """True when the coordinate falls inside Hong Kong territory.

    Cheap bbox rejection first, then the ray cast. A non-finite coordinate is
    rejected rather than raising: this is called from request paths, where a
    `NaN` that slipped past validation should be a clean refusal, not a 500.
    """
    if not (lat == lat and lng == lng):  # NaN
        return False
    if lat in (float("inf"), float("-inf")) or lng in (float("inf"), float("-inf")):
        return False
    (lat_lo, lat_hi), (lng_lo, lng_hi) = HK_BBOX
    if not (lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi):
        return False
    return any(_in_polygon(lat, lng, polygon) for polygon in HK_POLYGONS)

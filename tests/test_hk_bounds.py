"""The Hong Kong boundary check.

The test that matters most is the Shenzhen one. The box this replaced
(`lat 22.1-22.6, lng 113.8-114.5`) contained Shenzhen, so the check it was
supposed to perform would have admitted precisely the users it existed to stop.
Every Shenzhen case below is a real coordinate, and `test_the_old_bounding_box_
would_have_admitted_shenzhen` pins the original defect so it cannot come back.
"""

from __future__ import annotations

import pytest

from app.core.hk_bounds import HK_BBOX, HK_POLYGONS, is_in_hong_kong

# Genuine Hong Kong locations a user could plausibly be standing at. Drawn from
# every district and from the islands, because the failure mode of a coarse
# polygon is cutting off the edges — and the edges are where people live.
HONG_KONG = {
    "Central": (22.2819, 114.1582),
    "Tsim Sha Tsui": (22.2976, 114.1722),
    "Mong Kok": (22.3193, 114.1694),
    "Causeway Bay": (22.2800, 114.1850),
    "Wan Chai": (22.2770, 114.1730),
    "Kwun Tong": (22.3120, 114.2260),
    "Wong Tai Sin": (22.3420, 114.1960),
    "Lam Tin": (22.3090, 114.2360),
    "Tseung Kwan O": (22.3070, 114.2600),
    "Kowloon Tong": (22.3370, 114.1760),
    "Tsuen Wan": (22.3714, 114.1135),
    "Kwai Chung": (22.3570, 114.1300),
    "Tsing Yi": (22.3450, 114.1050),
    "Sha Tin": (22.3771, 114.1974),
    "Tai Po": (22.4501, 114.1642),
    "Yuen Long": (22.4445, 114.0227),
    "Tuen Mun": (22.3910, 113.9770),
    "Fanling": (22.4920, 114.1390),
    "Sheung Shui": (22.5010, 114.1270),
    "Sai Kung town": (22.3820, 114.2710),
    "Clear Water Bay": (22.3100, 114.2900),
    "Stanley": (22.2130, 114.2130),
    "Aberdeen": (22.2480, 114.1550),
    "HKU": (22.2830, 114.1370),
    "Tung Chung": (22.2890, 113.9420),
    "HK Airport": (22.3080, 113.9185),
    "Sunny Bay": (22.3300, 114.0300),
    "Disneyland": (22.3130, 114.0410),
    "Discovery Bay": (22.3010, 114.0170),
    "Mui Wo": (22.2650, 114.0000),
    "Tai O": (22.2540, 113.8520),
    "Cheung Chau": (22.2100, 114.0290),
    "Lamma (Yung Shue Wan)": (22.2210, 114.1120),
    "Peng Chau": (22.2860, 114.0370),
    "Ma Wan": (22.3510, 114.0620),
    "Tung Ping Chau": (22.5420, 114.4300),
    "Kat O": (22.5450, 114.2900),
    # Border crossings on the Hong Kong side. These are the points a tighter
    # northern boundary would wrongly refuse, and taxi drivers work at them.
    "Lok Ma Chau": (22.5150, 114.0660),
    "Lo Wu": (22.5280, 114.1130),
    "Man Kam To": (22.5320, 114.1330),
    "Sha Tau Kok (HK side)": (22.5430, 114.2130),
}

NOT_HONG_KONG = {
    # Shenzhen — the whole reason this module exists.
    "Shenzhen Futian": (22.5410, 114.0540),
    "Shenzhen Futian CBD": (22.5350, 114.0600),
    "Shenzhen Civic Center": (22.5450, 114.0550),
    "Shenzhen Luohu": (22.5480, 114.1230),
    "Shenzhen Huaqiangbei": (22.5470, 114.0860),
    "Shenzhen Nanshan": (22.5330, 113.9300),
    "Shenzhen Bao'an": (22.5550, 113.8830),
    "Shenzhen Shekou": (22.4820, 113.9160),
    "Shenzhen Yantian": (22.5580, 114.2400),
    "Shenzhen Airport": (22.6390, 113.8140),
    "Shenzhen Longgang": (22.7200, 114.2500),
    # Elsewhere.
    "Macau": (22.1987, 113.5439),
    "Zhuhai": (22.2710, 113.5770),
    "Guangzhou": (23.1291, 113.2644),
    "Dongguan": (23.0207, 113.7518),
    "Huizhou": (23.1115, 114.4160),
    "Taipei": (25.0330, 121.5654),
    "Tokyo": (35.6762, 139.6503),
    "London": (51.5074, -0.1278),
    "Null Island": (0.0, 0.0),
}


@pytest.mark.parametrize("name,coords", sorted(HONG_KONG.items()))
def test_hong_kong_locations_are_accepted(name: str, coords: tuple[float, float]):
    assert is_in_hong_kong(*coords), f"{name} {coords} should be inside Hong Kong"


@pytest.mark.parametrize("name,coords", sorted(NOT_HONG_KONG.items()))
def test_non_hong_kong_locations_are_refused(name: str, coords: tuple[float, float]):
    assert not is_in_hong_kong(*coords), f"{name} {coords} should be outside Hong Kong"


def test_the_old_bounding_box_would_have_admitted_shenzhen():
    """Pins the defect this module replaces.

    The old guard was `lat 22.1-22.6, lng 113.8-114.5`. If someone later
    "simplifies" the polygon back to a box, this test explains why they cannot:
    the box admits Shenzhen, and Shenzhen is what the check is for.
    """
    (lat_lo, lat_hi), (lng_lo, lng_hi) = ((22.1, 22.6), (113.8, 114.5))

    def old_box(lat: float, lng: float) -> bool:
        return lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi

    for name in ("Shenzhen Futian", "Shenzhen Luohu", "Shenzhen Bao'an"):
        lat, lng = NOT_HONG_KONG[name]
        assert old_box(lat, lng), f"{name} fell inside the old box"
        assert not is_in_hong_kong(lat, lng), f"{name} must be refused now"


def test_the_bbox_is_a_superset_of_every_polygon():
    """The bbox is only a fast reject, so it must never reject a real point.

    If a polygon had a vertex outside the bbox, the bbox would refuse
    coordinates that the polygon accepts — a false negative caused purely by
    the optimisation, which is the worst kind because it looks like a data
    problem rather than a bug.
    """
    (lat_lo, lat_hi), (lng_lo, lng_hi) = HK_BBOX
    for polygon in HK_POLYGONS:
        for point in polygon:
            assert lat_lo <= point.lat <= lat_hi, f"{point} outside bbox latitude"
            assert lng_lo <= point.lng <= lng_hi, f"{point} outside bbox longitude"


def test_every_polygon_has_at_least_three_vertices():
    """A ray cast against a degenerate ring gives meaningless answers."""
    for index, polygon in enumerate(HK_POLYGONS):
        assert len(polygon) >= 3, f"polygon {index} has {len(polygon)} vertices"


def test_nan_and_infinity_are_refused_not_raised():
    """These arrive from request paths, where a 500 is worse than a refusal."""
    assert not is_in_hong_kong(float("nan"), 114.15)
    assert not is_in_hong_kong(22.28, float("nan"))
    assert not is_in_hong_kong(float("inf"), 114.15)
    assert not is_in_hong_kong(22.28, float("-inf"))


def test_the_harbour_is_inside_hong_kong():
    """Victoria Harbour is Hong Kong, and no operator stands in it.

    Worth asserting because the harbour is water between two landmasses, so a
    polygon traced around coastlines rather than around the territory would
    exclude it — and a ferry passenger is a plausible user.
    """
    assert is_in_hong_kong(22.2930, 114.1690)  # mid-harbour, off TST


def test_a_ferry_passenger_between_the_islands_is_handled():
    """Documents the deliberate gap rather than pretending it is not there.

    The polygons are coarse, so open water between the outlying islands falls
    outside them. This is the accepted trade: a boat user is rare, and being
    refused is recoverable, whereas admitting Shenzhen is not.
    """
    # Mid-channel in the Adamasta Channel — the water the Lantau-Cheung Chau
    # ferry actually crosses, so this is the worst case of the gap, not a
    # contrived one. The earlier draft of this test used (22.22, 113.97) and
    # asserted the same thing; it passed only by accident of the polygon's
    # eastern edge, because that coordinate is on Lantau's south coast at Pui O,
    # not offshore at all. A test that "documents a gap" using a point that is
    # not in the gap documents nothing.
    assert not is_in_hong_kong(22.1900, 114.0350)

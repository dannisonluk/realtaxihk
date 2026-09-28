"""TDD Cycle 2 — RED: full fare estimate (tunnels, surcharges, discount, tip)."""
from decimal import Decimal as D

import pytest

from app.services.fare_calculator import TaxiType, Tunnel, calculate_fare

URBAN = TaxiType.URBAN
NT = TaxiType.NT
CH = Tunnel.CROSS_HARBOUR


def _by_code(bd, code):
    return next(s for s in bd.surcharges if s.code == code)


class TestCrossHarbour:
    def test_toll_plus_return_fee(self):
        # 10km+5min urban meter = 116.5; +25 toll +25 return
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="10", waiting_min=5,
            tunnels=(CH,), crosses_harbour=True,
        )
        assert bd.meter_fare == D("116.5")
        assert _by_code(bd, "tunnel_cross_harbour").amount == D("25")
        assert _by_code(bd, "cross_harbour_return").amount == D("25")
        assert bd.surcharges_total == D("50")
        assert bd.total_fare == D("166.5")

    def test_stand_pickup_waives_return_fee(self):
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="10", waiting_min=5,
            tunnels=(CH,), crosses_harbour=True,
            pickup_at_cross_harbour_stand=True,
        )
        assert bd.surcharges_total == D("25")
        assert bd.total_fare == D("141.5")

    def test_same_side_destination_waives_return_fee(self):
        # crossing tunnel used but destination on same side (e.g. cross to island
        # and back scenario is out of scope; here destination not across harbour)
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="2.5", waiting_min=0,
            tunnels=(CH,), crosses_harbour=False,
        )
        assert bd.surcharges_total == D("25")

    def test_crossing_without_cross_harbour_tunnel_raises(self):
        # claiming a harbour crossing with no cross-harbour tunnel specified is
        # a data contradiction and must be rejected
        with pytest.raises(ValueError):
            calculate_fare(taxi_type=URBAN, distance_km="5", crosses_harbour=True)


class TestLandTunnels:
    @pytest.mark.parametrize(
        "tunnel,amount",
        [
            (Tunnel.TAI_LAM, "28"),
            (Tunnel.TATES_CAIRN, "20"),
            (Tunnel.LION_ROCK, "8"),
            (Tunnel.EAGLES_NEST, "8"),
            (Tunnel.SHING_MUN, "8"),
            (Tunnel.ABERDEEN, "8"),
            (Tunnel.LANTAU_LINK, "30"),
        ],
    )
    def test_single_tunnel(self, tunnel, amount):
        bd = calculate_fare(taxi_type=URBAN, distance_km="5", tunnels=(tunnel,))
        assert bd.surcharges_total == D(amount)

    def test_multiple_tunnels_sum_and_dedupe(self):
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="9",
            tunnels=(Tunnel.TAI_LAM, Tunnel.TAI_LAM, Tunnel.LION_ROCK),
        )
        # meter 9km = 102.5 + 28 + 8
        assert bd.meter_fare == D("102.5")
        assert bd.surcharges_total == D("36")


class TestOtherSurcharges:
    def test_baggage_animals_booking(self):
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="3",
            baggage_count=2, animals=1, advance_booking=True,
        )
        # meter 3km = 39.5 + 12 + 5 + 5
        assert bd.meter_fare == D("39.5")
        assert bd.surcharges_total == D("22")
        assert bd.total_fare == D("61.5")

    @pytest.mark.parametrize("kwargs", [
        {"baggage_count": -1}, {"animals": -1}, {"baggage_count": 99},
    ])
    def test_invalid_counts_raise(self, kwargs):
        with pytest.raises(ValueError):
            calculate_fare(taxi_type=URBAN, distance_km="3", **kwargs)


class TestDiscountAndTip:
    def test_85_discount_applies_to_meter_only(self):
        # meter 116.5; 15% off = 17.475 -> 17.5 (ROUND_HALF_UP 0.1)
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="10", waiting_min=5,
            tunnels=(CH,), crosses_harbour=True, discount_percent="15",
        )
        assert bd.meter_fare == D("116.5")
        assert bd.meter_discount == D("17.5")
        assert bd.meter_after_discount == D("99.0")
        # tunnels untouched: 50
        assert bd.surcharges_total == D("50")
        assert bd.total_fare == D("149.0")

    def test_discount_rounding_half_up(self):
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="3", waiting_min="1.2",
            discount_percent="15",
        )
        # meter 43.7 * 0.15 = 6.555 -> 6.6 ; after = 37.1
        assert bd.meter_discount == D("6.6")
        assert bd.meter_after_discount == D("37.1")
        assert bd.total_fare == D("37.1")

    def test_zero_discount_is_noop(self):
        bd = calculate_fare(taxi_type=URBAN, distance_km="4", discount_percent=0)
        assert bd.meter_discount == D("0.0")
        assert bd.meter_after_discount == bd.meter_fare

    def test_tip_added_after_discount(self):
        bd = calculate_fare(
            taxi_type=URBAN, distance_km="4", discount_percent="10", tip="10",
        )
        # meter 50 -> 45 after; + tip 10
        assert bd.total_fare == D("55.0")

    @pytest.mark.parametrize("bad", ["-5", "101", "150"])
    def test_invalid_discount_raises(self, bad):
        with pytest.raises(ValueError):
            calculate_fare(taxi_type=URBAN, distance_km="4", discount_percent=bad)

    def test_negative_tip_raises(self):
        with pytest.raises(ValueError):
            calculate_fare(taxi_type=URBAN, distance_km="4", tip="-1")


class TestNTTaxiCrossHarbour:
    def test_nt_meter_and_toll(self):
        # NT 8km = exactly 82.5 (threshold); + 50 cross-harbour
        bd = calculate_fare(
            taxi_type=NT, distance_km="8", tunnels=(CH,), crosses_harbour=True,
        )
        assert bd.meter_fare == D("82.5")
        assert bd.total_fare == D("132.5")


class TestComplianceFields:
    def test_estimate_disclaimer_present(self):
        bd = calculate_fare(taxi_type=URBAN, distance_km="5")
        assert bd.is_estimate is True
        assert "estimat" in bd.disclaimer_en.lower()
        assert "僅供參考" in bd.disclaimer_zh
        assert "自願" in bd.disclaimer_zh

    def test_tariff_version_reported(self):
        bd = calculate_fare(taxi_type=URBAN, distance_km="5")
        assert "2024-07-14" in bd.tariff_version

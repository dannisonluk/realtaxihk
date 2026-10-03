"""TDD Cycle 1 — RED: meter fare engine against the official 2024-07-14 tariff.

All expected values hand-derived from the tariff table and the ceiling rule
("每200米或其部分／每分鐘或其部分" = any part counts as a full jump).
"""

from decimal import Decimal as D

import pytest

from app.services.order.fare_calculator import TaxiType, estimate_meter_fare

URBAN = TaxiType.URBAN
NT = TaxiType.NT
LANTAU = TaxiType.LANTAU


class TestUrbanMeter:
    @pytest.mark.parametrize(
        "km,wait,expected",
        [
            ("1.5", "0", "29.0"),  # within flagfall
            ("2", "0", "29.0"),  # exactly 2km still flagfall
            ("2.05", "0", "31.1"),  # any part of 200m = 1 jump
            ("2.4", "0", "33.2"),  # exactly 2 jumps
            ("2.400001", "0", "35.3"),  # epsilon over = 3rd jump
            ("4", "0", "50.0"),  # 10 jumps x 2.1
            ("9", "0", "102.5"),  # 35 jumps = tier boundary exactly
            ("9.2", "0", "103.9"),  # 36th jump at reduced rate 1.4
            ("25", "0", "214.5"),  # 115 jumps: 29+35*2.1+80*1.4
        ],
    )
    def test_meter_table(self, km, wait, expected):
        assert estimate_meter_fare(URBAN, D(km), D(wait)) == D(expected)

    def test_wait_only_trip_still_charges_flagfall(self):
        # 0km, 3 min: flagfall + 3 wait jumps
        assert estimate_meter_fare(URBAN, 0, 3) == D("35.3")

    def test_wait_jumps_also_tier_at_threshold(self):
        # 0km, 100min: 35 jumps x2.1 -> 102.5, then 65 x1.4 = 91.0
        assert estimate_meter_fare(URBAN, 0, 100) == D("193.5")

    def test_distance_plus_wait_combined(self):
        # 3km = 5 jumps (39.5) + 5 wait jumps, all below threshold: 10 x 2.1
        assert estimate_meter_fare(URBAN, D("3"), 5) == D("50.0")

    def test_partial_wait_minute_ceil(self):
        # 3km (5 jumps) + 1.2min -> 2 wait jumps ("每分鐘或其部分") = 7 x 2.1
        assert estimate_meter_fare(URBAN, D("3"), D("1.2")) == D("43.7")

    def test_float_inputs_normalized(self):
        assert estimate_meter_fare(URBAN, 4.0) == D("50.0")
        assert estimate_meter_fare(URBAN, "4", 0) == D("50.0")


class TestNTMeter:
    @pytest.mark.parametrize(
        "km,expected",
        [
            ("2", "25.5"),
            ("5", "54.0"),  # 15 x 1.9
            ("8", "82.5"),  # 30 x 1.9 = boundary exactly
            ("8.2", "83.9"),  # 31st jump at 1.4
            ("12", "110.5"),  # 50 jumps: 25.5+57+20*1.4
        ],
    )
    def test_meter_table(self, km, expected):
        assert estimate_meter_fare(NT, D(km), 0) == D(expected)


class TestLantauMeter:
    @pytest.mark.parametrize(
        "km,expected",
        [
            ("2", "24.0"),
            ("5", "52.5"),  # 15 x 1.9
            ("20", "195.0"),  # 90 x 1.9 = boundary exactly
            ("20.2", "196.6"),  # 91st jump at 1.6
        ],
    )
    def test_meter_table(self, km, expected):
        assert estimate_meter_fare(LANTAU, D(km), 0) == D(expected)


class TestTariffConsistency:
    """The published tier thresholds must be reachable exactly by whole jumps."""

    def test_urban_threshold_reached_exactly(self):
        assert D("29") + 35 * D("2.1") == D("102.5")

    def test_nt_threshold_reached_exactly(self):
        assert D("25.5") + 30 * D("1.9") == D("82.5")

    def test_lantau_threshold_reached_exactly(self):
        assert D("24") + 90 * D("1.9") == D("195.0")


class TestValidation:
    @pytest.mark.parametrize(
        "km,wait",
        [("-1", 0), (0, "-5"), ("101", 0)],  # negative / negative / over HK max
    )
    def test_invalid_inputs_raise(self, km, wait):
        with pytest.raises(ValueError):
            estimate_meter_fare(URBAN, D(km), D(wait))

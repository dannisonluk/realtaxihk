"""Unit tests for the three money/ratio wire formatters.

These are pure functions with no DB and no HTTP, so they are tested directly
rather than through an endpoint. That matters here because the defects they
guard against were *rounding-mode* defects: a whole-dollar integration test
passes under both `ROUND_HALF_UP` and `ROUND_HALF_EVEN`, so only a tie value
can tell them apart. Testing at the boundary is the only place a tie is visible.

The three formatters are not interchangeable and the tests pin the distinction:

* `money_str`  — 2 dp, for a value stored in a `Numeric(10, 2)` column.
* `meter_str`  — 1 dp, for a value read off the tariff table.
* `ratio_str`  — 2 dp, for a derived figure (a mean, a rate, a distance).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import meter_str, money_str, ratio_str


class TestHalfUpIsTheRuleEverywhere:
    """A tie must round away from zero in all three formatters.

    `Decimal.quantize` defaults to `ROUND_HALF_EVEN`, which sends a tie to the
    *even* digit — `150.45 -> 150.4` but `150.55 -> 150.6`. A formatter that
    forgets to pass `rounding=` therefore disagrees with its neighbour on half
    of all tie values, and agrees on the other half, which is what makes the bug
    hard to spot by sampling.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("150.45", "150.5"),  # half-even would give 150.4
            ("150.55", "150.6"),  # half-even agrees here
            ("0.15", "0.2"),
            ("-150.45", "-150.5"),
        ],
    )
    def test_meter_str_rounds_half_up(self, raw, expected):
        assert meter_str(Decimal(raw)) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("150.445", "150.45"),  # half-even would give 150.44
            ("150.455", "150.46"),  # half-even agrees here
            ("0.005", "0.01"),  # half-even would give 0.00
            ("-0.005", "-0.01"),
        ],
    )
    def test_money_str_rounds_half_up(self, raw, expected):
        assert money_str(Decimal(raw)) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.005", "1.01"),  # the exact live bug: half-even gave "1.00"
            ("2.675", "2.68"),  # half-even would give 2.67
            ("0.005", "0.01"),
            ("-1.005", "-1.01"),
        ],
    )
    def test_ratio_str_rounds_half_up(self, raw, expected):
        """The regression this function was added to close.

        Every ratio in the analytics payload was `quantize(Decimal("0.01"))`
        with the default mode, so it rounded half-to-even while the money in the
        *same response* — and the totals summed from those rendered strings —
        rounded half-up. `1.005` read `"1.00"` in one field and `"1.01"` in
        another.
        """
        assert ratio_str(raw) == expected


class TestRatioStrAcceptsTheTypesCallSitesActuallyPass:
    """The call sites pass three different types; all three must work.

    Requiring the caller to convert invites the exact `int.quantize`
    `AttributeError` that the accumulator in `analytics_service` already
    carries a comment about, so `ratio_str` widens its input instead.
    """

    def test_decimal(self):
        assert ratio_str(Decimal("3.14159")) == "3.14"

    def test_int(self):
        """A row count divided by nothing — `ratios` divide, but `sum()` of an
        empty bucket list returns the *int* `0`."""
        assert ratio_str(0) == "0.00"
        assert ratio_str(7) == "7.00"

    def test_str(self):
        """A value the caller previously rendered and is now re-reading."""
        assert ratio_str("2.5") == "2.50"
        assert ratio_str("2.505") == "2.51"

    def test_division_of_two_decimals(self):
        """The most common shape: a mean computed from two DB `Decimal`s."""
        assert ratio_str(Decimal(10) / Decimal(4)) == "2.50"

    def test_always_emits_two_decimals(self):
        """`"7.0"` and `"7.00"` in the same payload is a wire-form bug, not a
        cosmetic one — the console parses and re-displays these."""
        for value in ("7", "7.0", "7.00", Decimal("7")):
            assert ratio_str(value) == "7.00"


class TestRatioStrIsNotMoneyStr:
    """Both are 2 dp half-up, but they are separate names on purpose.

    A reader seeing `money_str` on a trip count would conclude the number is a
    monetary amount. Keeping the names distinct is what makes the intent legible
    at the call site; the shared rule is a coincidence of precision, not a
    reason to merge them.
    """

    def test_same_rule_same_output(self):
        for raw in ("1.005", "2.675", "0.005", "1234.567"):
            assert ratio_str(raw) == money_str(Decimal(raw))

    def test_meter_is_a_different_precision(self):
        """`meter_str` is not a low-precision `money_str` — 1 dp vs 2 dp.

        `1.25` is a tie at 1 dp and rounds half-up to `1.3`, which is the value
        the tariff engine itself would produce. The point of the assertion is
        the *precision* difference, not a rounding difference: the same input
        gives `1.25` at 2 dp because there is no tie to resolve there.
        """
        assert meter_str(Decimal("1.25")) == "1.3"
        assert money_str(Decimal("1.25")) == "1.25"
        assert ratio_str("1.25") == "1.25"

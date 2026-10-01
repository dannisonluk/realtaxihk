"""Money formatting — two precisions, and they are not interchangeable.

HKD has no sub-cent usage, so `Decimal` is the only correct type for it. What
differs across the platform is *how many decimals a figure is exact to*, and
that depends on where the number came from:

**Stored money — 2 dp (`money_str`)**
Every money column in `app/models/__init__.py` is `Numeric(10, 2)`: deposits,
ledger amounts, `balance_after`, refunds, order totals, fleet settlements. The
database keeps cents exactly, so the wire form must too. Serialising these at
1 dp silently destroyed stored precision *after* storage — a `0.05` balance left
the API as `"0.1"` and rendered as `HK$0.10`, and `0.01` left as `"0.0"` and
rendered as `HK$0.00`, i.e. a real credit displayed as nothing.

**Meter figures — 1 dp (`meter_str`)**
A fare is computed from a tariff table whose increments are whole deciles of a
dollar (`fare_calculator` quantises to `Decimal("0.1")`), so 1 dp is the exact
precision of the input. `/fare/calculate` and the `fare` snapshot frozen onto an
order are the same reading and must render identically.

`ROUND_HALF_UP` is deliberate for both. `Decimal.quantize` defaults to
`ROUND_HALF_EVEN` (banker's rounding), which turns a tie toward the even digit —
`150.45 -> 150.4`. The fare engine already rounds `150.45 -> 150.5`, so leaving
the default in place would make two views of one number disagree.

**Ratios and other derived figures — 2 dp half-up (`ratio_str`)**
A derived figure (trips per day, average distance, a percentage) is neither
stored money nor a meter reading, but it still needs *one* rounding rule. It had
none: the call sites wrote `Decimal(x).quantize(Decimal("0.01"))`, which is
`ROUND_HALF_EVEN` by default — so a ratio rounded the opposite way from the
money it sat next to, in the same response. `ratio_str` is the single entry
point so that cannot drift again.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_TICK = Decimal("0.1")


def money_str(v: Decimal) -> str:
    """Canonical 2-dp wire form for a **stored** money value.

    Accepts anything `Decimal()` accepts (a `Decimal` from the DB, a string, an
    int). Always emits exactly two decimal places, so `500` -> `"500.00"` rather
    than `"500.0"` in some places and `"500.00"` in others.
    """
    return str(Decimal(v).quantize(_CENT, rounding=ROUND_HALF_UP))


def meter_str(v: Decimal) -> str:
    """Canonical 1-dp wire form for a **meter** figure (fare, toll, surcharge).

    Not a lower-precision `money_str`: the two answer different questions. Use
    this only for values derived from the tariff table; anything persisted in a
    `Numeric(10, 2)` column wants `money_str`.
    """
    return str(Decimal(v).quantize(_TICK, rounding=ROUND_HALF_UP))


def ratio_str(v: Decimal | int | str) -> str:
    """Canonical 2-dp wire form for a **derived** figure, half-up.

    For values that are computed rather than stored: means, rates, distances,
    counts per period. They are not money, so `money_str` would be a lie about
    what the number is; but they appear beside money in the same payload, so
    they must round the same way.

    The bug this closes is subtle and was live: `Decimal.quantize` defaults to
    `ROUND_HALF_EVEN`, so the analytics ratios rounded half-to-even while every
    money figure on the same screen — and the `sum()` totals derived *from*
    those rendered strings — rounded half-up. `1.005` became `"1.00"` here and
    `"1.01"` everywhere else. Both are defensible independently; having both in
    one response is not, because the same underlying quantity could read two
    ways depending on which field a reader looked at.

    Accepts `Decimal | int | str` rather than only `Decimal`. The call sites
    divide two `Decimal`s (a `Decimal`), count rows (an `int`), and re-read a
    value they previously rendered to a string (a `str`); requiring the caller
    to convert invites exactly the `int.quantize` AttributeError that the
    accumulator in `analytics_service` already carries a comment about.
    """
    return str(Decimal(v).quantize(_CENT, rounding=ROUND_HALF_UP))

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

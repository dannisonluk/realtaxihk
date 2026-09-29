"""Money formatting — HKD, canonical wire precision = $0.1 (smallest meter tick)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.1")


def money_str(v: Decimal) -> str:
    return str(Decimal(v).quantize(_CENT, rounding=ROUND_HALF_UP))

"""Fare estimation API. Every response carries the Cap. 374D disclaimer fields.

Security (SEC-09/15): `tunnels` is capped at the 8 real tunnel values — unbounded
it let an unauthenticated caller turn one small request into a 32.8 MB validation
error body — and the endpoint is rate-limited per IP, since it is public and
CPU-bound.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.schemas import FareEstimateOut, FareSurchargeOut
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError
from app.core.money import MoneyInput, meter_str
from app.services.order.fare_calculator import TaxiType, Tunnel, calculate_fare

router = APIRouter(prefix="/api/v1/fare", tags=["fare"])

_MAX_TUNNELS = 8  # the Tunnel enum has exactly 8 members


def _meter_str(v: MoneyInput) -> str:
    """Fare figures, at the meter's own 1-dp tick.

    Delegates to `app.core.money.meter_str`, which owns the rounding rule. Kept
    as a module-local alias because every call site in this file is a meter
    reading, and the short name keeps the response construction readable.
    """
    return meter_str(v)


class FareEstimateRequest(BaseModel):
    taxi_type: TaxiType
    distance_km: Decimal = Field(ge=0, le=100)
    waiting_min: Decimal = Field(default=Decimal("0"), ge=0, le=600)
    tunnels: list[Tunnel] = Field(default_factory=list, max_length=_MAX_TUNNELS)
    pickup_at_cross_harbour_stand: bool = False
    crosses_harbour: bool = False
    baggage_count: int = Field(default=0, ge=0, le=10)
    animals: int = Field(default=0, ge=0, le=5)
    advance_booking: bool = False
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tip: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("500"))  # B3: cap

    @field_validator("tunnels", mode="before")
    @classmethod
    def cap_tunnels(cls, v):
        """Reject an over-long list BEFORE the items are validated.

        `max_length` alone is not enough for the DoS case (SEC-09): if the items
        are validated first, 100k bogus tunnel names each produce their own error
        object and the 422 body balloons to tens of megabytes — the very
        amplification the cap was meant to stop. A `before` validator runs on the
        raw input, so one oversized request yields exactly one error.
        """
        if isinstance(v, (list, tuple)) and len(v) > _MAX_TUNNELS:
            raise ValueError(f"at most {_MAX_TUNNELS} tunnels may be listed")
        return v

    @field_validator("distance_km", "waiting_min", "discount_percent", "tip")
    @classmethod
    def finite_non_negative(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("must be a finite number")
        return v


@router.post("/estimate", response_model=FareEstimateOut)
async def estimate_fare(
    payload: FareEstimateRequest,
    request: Request,
) -> FareEstimateOut:
    settings = get_settings()
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"fare:estimate:ip:{client_ip(request)}",
        settings.fare_estimate_ip_rate_limit,
        settings.fare_estimate_ip_window_s,
    ):
        raise HTTPException(status_code=429, detail="too many fare estimates, slow down")

    try:
        breakdown = calculate_fare(
            taxi_type=payload.taxi_type,
            distance_km=payload.distance_km,
            waiting_min=payload.waiting_min,
            tunnels=payload.tunnels,
            pickup_at_cross_harbour_stand=payload.pickup_at_cross_harbour_stand,
            crosses_harbour=payload.crosses_harbour,
            baggage_count=payload.baggage_count,
            animals=payload.animals,
            advance_booking=payload.advance_booking,
            discount_percent=payload.discount_percent,
            tip=payload.tip,
        )
    except BusinessRuleError:
        # Already carries machine-readable `details`; `BusinessRuleError`
        # subclasses `ValueError`, so the branch below would discard them.
        raise
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    return FareEstimateOut(
        taxi_type=breakdown.taxi_type,
        distance_km=breakdown.distance_km,
        waiting_min=breakdown.waiting_min,
        meter_fare=_meter_str(breakdown.meter_fare),
        discount_percent=str(breakdown.discount_percent),
        meter_discount=_meter_str(breakdown.meter_discount),
        meter_after_discount=_meter_str(breakdown.meter_after_discount),
        surcharges=[
            FareSurchargeOut(
                code=s.code,
                name_en=s.name_en,
                name_zh=s.name_zh,
                amount=_meter_str(s.amount),
            )
            for s in breakdown.surcharges
        ],
        surcharges_total=_meter_str(breakdown.surcharges_total),
        tip=_meter_str(breakdown.tip),
        total_fare=_meter_str(breakdown.total_fare),
        tariff_version=breakdown.tariff_version,
        is_estimate=breakdown.is_estimate,
        disclaimer_en=breakdown.disclaimer_en,
        disclaimer_zh=breakdown.disclaimer_zh,
    )

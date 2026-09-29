"""Fare estimation API. Every response carries the Cap. 374D disclaimer fields."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.core.exceptions import BusinessRuleError
from app.services.fare_calculator import TaxiType, Tunnel, calculate_fare

router = APIRouter(prefix="/api/v1/fare", tags=["fare"])

_CENT = Decimal("0.1")  # smallest meter tick; canonical wire precision


def _money_str(v: Decimal) -> str:
    return str(v.quantize(_CENT))


class FareEstimateRequest(BaseModel):
    taxi_type: TaxiType
    distance_km: Decimal = Field(ge=0, le=100)
    waiting_min: Decimal = Field(default=Decimal("0"), ge=0, le=600)
    tunnels: list[Tunnel] = []
    pickup_at_cross_harbour_stand: bool = False
    crosses_harbour: bool = False
    baggage_count: int = Field(default=0, ge=0, le=10)
    animals: int = Field(default=0, ge=0, le=5)
    advance_booking: bool = False
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tip: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("500"))  # B3: cap

    @field_validator("distance_km", "waiting_min", "discount_percent", "tip")
    @classmethod
    def finite_non_negative(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("must be a finite number")
        return v


class FareEstimateResponse(BaseModel):
    taxi_type: TaxiType
    distance_km: Decimal
    waiting_min: Decimal
    meter_fare: str
    discount_percent: str
    meter_discount: str
    meter_after_discount: str
    surcharges: list[dict]
    surcharges_total: str
    tip: str
    total_fare: str
    tariff_version: str
    is_estimate: bool
    disclaimer_en: str
    disclaimer_zh: str


@router.post("/estimate", response_model=FareEstimateResponse)
async def estimate_fare(payload: FareEstimateRequest) -> FareEstimateResponse:
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
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    return FareEstimateResponse(
        taxi_type=breakdown.taxi_type,
        distance_km=breakdown.distance_km,
        waiting_min=breakdown.waiting_min,
        meter_fare=_money_str(breakdown.meter_fare),
        discount_percent=str(breakdown.discount_percent),
        meter_discount=_money_str(breakdown.meter_discount),
        meter_after_discount=_money_str(breakdown.meter_after_discount),
        surcharges=[
            {
                "code": s.code,
                "name_en": s.name_en,
                "name_zh": s.name_zh,
                "amount": _money_str(s.amount),
            }
            for s in breakdown.surcharges
        ],
        surcharges_total=_money_str(breakdown.surcharges_total),
        tip=_money_str(breakdown.tip),
        total_fare=_money_str(breakdown.total_fare),
        tariff_version=breakdown.tariff_version,
        is_estimate=breakdown.is_estimate,
        disclaimer_en=breakdown.disclaimer_en,
        disclaimer_zh=breakdown.disclaimer_zh,
    )

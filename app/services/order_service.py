"""Order domain service — creation with fare snapshot + lifecycle transitions.

Cap. 374D: the fare snapshot (with disclaimers) is frozen into fare_json at
creation time so historical orders stay auditable after tariff changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import meter_str, money_str
from app.models import Order, OrderStatus
from app.services.fare_calculator import TaxiType, Tunnel, calculate_fare
from app.services.state_machine import assert_order_transition


def _meter_str(v: Decimal) -> str:
    """Fare figures inside the order snapshot — the meter's 1-dp rule."""
    return meter_str(v)


def _point_wkt(lat: float, lng: float) -> str:
    return f"POINT({lng} {lat})"


def fare_snapshot(bd, tunnels: list[str] | None = None, crosses_harbour: bool = False) -> dict:
    """The fare breakdown frozen onto an order, in the *meter's* 1-dp form.

    Two different roundings live in this module and they are not
    interchangeable. A fare is a meter reading — the tariff table's increments
    are whole deciles, so it serialises at 1 dp (`_meter_str`). The order's
    *stored* total is `Numeric(10,2)` and serialises at 2 dp (`money_str`).

    This snapshot is the `fare` field of the order API, and the passenger
    compares it against a live `/fare/calculate` response. Both are fare
    figures, so both must use the meter rule — the snapshot previously used
    `money_str`, which emitted `134.00` where the estimate endpoint emitted
    `134.0` for the same trip.
    """
    return {
        "meter_fare": _meter_str(bd.meter_fare),
        "meter_after_discount": _meter_str(bd.meter_after_discount),
        "surcharges_total": _meter_str(bd.surcharges_total),
        "tip": _meter_str(bd.tip),
        "total_fare": _meter_str(bd.total_fare),
        "discount_percent": str(bd.discount_percent),
        "tunnels": list(tunnels or []),
        "crosses_harbour": crosses_harbour,
        "tariff_version": bd.tariff_version,
        "is_estimate": True,
        "disclaimer_en": bd.disclaimer_en,
        "disclaimer_zh": bd.disclaimer_zh,
        "surcharges": [
            {
                "code": s.code,
                "name_en": s.name_en,
                "name_zh": s.name_zh,
                "amount": _meter_str(s.amount),
            }
            for s in bd.surcharges
        ],
    }


class OrderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, passenger_user_id, payload) -> Order:
        # Validate + canonicalise first: SEC-11 — the snapshot stores the deduped
        # set of valid tunnel codes, never the raw caller-supplied list. The raw
        # list used to be written through verbatim, so an order could carry
        # megabytes of junk into fare_json and re-send it on every list call.
        tunnel_enums = [Tunnel(t) for t in payload.tunnels]
        canonical_tunnels = sorted({t.value for t in tunnel_enums})
        bd = calculate_fare(
            taxi_type=TaxiType(payload.taxi_type),
            distance_km=Decimal(payload.distance_km),
            waiting_min=Decimal(payload.waiting_min),
            tunnels=tunnel_enums,
            crosses_harbour=payload.crosses_harbour,
            pickup_at_cross_harbour_stand=payload.pickup_at_cross_harbour_stand,
            discount_percent=Decimal(payload.discount_percent),
            tip=Decimal(payload.tip),
        )
        snapshot = fare_snapshot(
            bd,
            tunnels=canonical_tunnels,
            crosses_harbour=payload.crosses_harbour,
        )
        order = Order(
            passenger_id=passenger_user_id,
            status=OrderStatus.BROADCASTING,
            pickup_location=_point_wkt(payload.pickup_lat, payload.pickup_lng),
            pickup_address=payload.pickup_address,
            dropoff_location=_point_wkt(payload.dropoff_lat, payload.dropoff_lng),
            dropoff_address=payload.dropoff_address,
            distance_km=Decimal(payload.distance_km),
            taxi_type=payload.taxi_type,
            fare_json=snapshot,
            tariff_version=bd.tariff_version,
            estimated_total_hkd=Decimal(money_str(bd.total_fare)),
            discount_percent=Decimal(payload.discount_percent),
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def transition(self, order: Order, target: OrderStatus) -> Order:
        assert_order_transition(order.status, target)
        order.status = target
        now = datetime.now(UTC)
        if target == OrderStatus.ACCEPTED:
            order.accepted_at = now
        elif target == OrderStatus.DRIVER_ARRIVED:
            order.driver_arrived_at = now
        elif target == OrderStatus.COMPLETED:
            order.completed_at = now
        elif target == OrderStatus.CANCELLED:
            order.cancelled_at = now
        await self.session.flush()
        return order


def order_out(order: Order) -> dict:
    return {
        "id": str(order.id),
        "status": order.status.value,
        "taxi_type": order.taxi_type,
        "fare": order.fare_json,
        "estimated_total_hkd": money_str(Decimal(order.estimated_total_hkd)),
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }

"""Order domain service — creation with fare snapshot + lifecycle transitions.

Cap. 374D: the fare snapshot (with disclaimers) is frozen into fare_json at
creation time so historical orders stay auditable after tariff changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import MoneyInput, meter_str, money_str
from app.core.region import destination_area, match_premium_destination
from app.models import (
    DestinationStatus,
    Order,
    OrderFareMode,
    OrderStatus,
    PaymentMethod,
    PremiumDestination,
)
from app.services.fare.fixed_fare_service import FixedFareService
from app.services.order.fare_calculator import TaxiType, Tunnel, calculate_fare
from app.services.order.state_machine import assert_order_transition


def _meter_str(v: MoneyInput) -> str:
    """Fare figures inside the order snapshot — the meter's 1-dp rule."""
    return meter_str(v)


def _point_wkt(lat: float, lng: float) -> str:
    return f"POINT({lng} {lat})"


def _point_from_wkb(value) -> tuple[float, float] | None:
    """Best-effort point decode without a Shapely dependency.

    PostGIS hands back a `WKBElement` when the row is read through the ORM.
    Parsing the stored bytes directly keeps `geoalchemy2` unextended: the
    project deliberately does not depend on `geoalchemy2[shapely]`.
    """
    if value is None:
        return None
    if hasattr(value, "data") and isinstance(value.data, (bytes, bytearray, memoryview)):
        data = bytes(value.data)
        if len(data) < 25:
            return None
        byte_order = data[0]
        marker = "<" if byte_order else ">"
        try:
            import struct

            x, y = struct.unpack(f"{marker}dd", data[9:25])
            return y, x  # PostGIS stores POINT(lng lat)
        except (struct.error, ValueError):
            return None
    text = str(value).strip()
    if text.upper().startswith("POINT") and "(" in text and ")" in text:
        inner = text[text.index("(") + 1 : text.rindex(")")]
        parts = inner.split()
        if len(parts) == 2:
            try:
                return float(parts[1]), float(parts[0])
            except ValueError:
                return None
    return None


def _point_lat(value) -> float | None:
    point = _point_from_wkb(value)
    return point[0] if point else None


def _point_lng(value) -> float | None:
    point = _point_from_wkb(value)
    return point[1] if point else None


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

        # Phase 1: canonicalise ride requirements and remember the passenger's
        # payment preference. The animal detail is stored so the assigned
        # driver sees species and approximate size before accepting.
        requirements = None
        if payload.requirements is not None:
            requirements = payload.requirements.model_dump(mode="json")
        payment_preference = []
        for m in payload.payment_preference:
            payment_preference.append(PaymentMethod(m).value)

        # Destination area + premium destination are computed server-side from
        # the dropoff coordinates. The platform never trusts a client-tagged
        # premium destination.
        pickup_area = destination_area(payload.pickup_lat, payload.pickup_lng)
        dropoff_area = destination_area(payload.dropoff_lat, payload.dropoff_lng)
        premium_match = None
        if dropoff_area is not None:
            active = (
                (
                    await self.session.execute(
                        select(PremiumDestination).where(
                            PremiumDestination.status == DestinationStatus.ACTIVE.value
                        )
                    )
                )
                .scalars()
                .all()
            )
            premium_match = match_premium_destination(
                payload.dropoff_lat,
                payload.dropoff_lng,
                active,
            )

        order = Order(
            passenger_id=passenger_user_id,
            status=OrderStatus.BROADCASTING,
            fare_mode=OrderFareMode.METER,
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
            requirements_json=requirements,
            payment_preference_json=(
                {"methods": payment_preference} if payment_preference else None
            ),
            pickup_area=pickup_area,
            destination_area=dropoff_area,
            premium_destination_id=(uuid.UUID(premium_match.id) if premium_match else None),
            premium_destination_json=(
                {
                    "id": premium_match.id,
                    "code": premium_match.code,
                    "name_zh": premium_match.name_zh,
                    "name_en": premium_match.name_en,
                    "avatar_key": premium_match.avatar_key,
                }
                if premium_match
                else None
            ),
        )
        self.session.add(order)
        await self.session.flush()

        # Fixed-fare matching (一口價). If a standing offer covers this route
        # and is competitive, the order freezes the passenger price
        # (`offer + platform fee`) and binds the order to that offer.
        match = await FixedFareService(self.session).match_order(
            order,
            Decimal(bd.total_fare),
            pickup_area=pickup_area,
        )
        if match is not None:
            order.fare_mode = OrderFareMode.FIXED
            order.fixed_offer_id = match.offer.id
            order.driver_price_hkd = match.offer.price_hkd
            order.platform_fee_hkd = match.platform_fee_hkd
            order.passenger_price_hkd = match.passenger_price_hkd
            order.estimated_total_hkd = match.passenger_price_hkd
            snapshot.update(
                {
                    "fare_mode": OrderFareMode.FIXED.value,
                    "driver_price_hkd": money_str(match.offer.price_hkd),
                    "platform_fee_hkd": money_str(match.platform_fee_hkd),
                    "passenger_price_hkd": money_str(match.passenger_price_hkd),
                }
            )
            order.fare_json = snapshot
        else:
            snapshot.update({"fare_mode": OrderFareMode.METER.value})
            order.fare_json = snapshot

        await self.session.flush()
        return order

    async def transition(self, order: Order, target: OrderStatus) -> Order:
        """Move an order to `target`, stamping the timestamp that belongs to it.

        This is the single writer of `Order.status`, so it is also where the
        state machine's data invariants are enforced rather than merely asserted
        by tests:

        * `DRIVER_ARRIVED` writes `driver_arrived_at` **and**
          `arrival_confirmed_at` together, because arrival is only ever reached
          through the passenger's confirmation — a status claiming arrival with
          no proof is the defect the two-step flow exists to stop.
        * The "first time only" stamps (`accepted_at`, `started_at`) are not
          overwritten on a re-entry. `PENDING_ARRIVAL_CONFIRM -> ACCEPTED`
          (three failed confirmations) and `DESTINATION_CHANGED -> IN_TRIP` are
          both legal returns, and re-stamping would rewrite history.
        """
        assert_order_transition(order.status, target)
        order.status = target
        now = datetime.now(UTC)
        if target == OrderStatus.ACCEPTED:
            order.accepted_at = order.accepted_at or now
        elif target == OrderStatus.PENDING_ARRIVAL_CONFIRM:
            order.arrival_claimed_at = now
        elif target == OrderStatus.DRIVER_ARRIVED:
            order.driver_arrived_at = now
            order.arrival_confirmed_at = now
        elif target == OrderStatus.IN_TRIP:
            order.started_at = order.started_at or now
        elif target == OrderStatus.DESTINATION_CHANGED:
            order.destination_changed_at = now
        elif target == OrderStatus.INTERRUPTED:
            order.interrupted_at = now
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
        # Phase 1: ride requirements and driver visibility data, all optional.
        "requirements": order.requirements_json,
        "payment_preference": (order.payment_preference_json or {}).get("methods", []),
        "driver_payment_methods": (order.driver_payment_methods_json or {}).get("methods", []),
        "premium_destination": order.premium_destination_json,
        "destination_area": order.destination_area,
        "pickup_area": order.pickup_area,
        # Phase 3: route template. PostGIS stores `POINT(lng lat)` WKT, and
        # without `geoalchemy2[shapely]` the safest read path is to parse that
        # text rather than touch the WKBElement half. Old rows keep lat/lng
        # null until a client re-enters the route.
        "pickup_lat": _point_lat(order.pickup_location),
        "pickup_lng": _point_lng(order.pickup_location),
        "pickup_address": order.pickup_address if order.pickup_address else None,
        "dropoff_lat": _point_lat(order.dropoff_location),
        "dropoff_lng": _point_lng(order.dropoff_location),
        "dropoff_address": order.dropoff_address if order.dropoff_address else None,
        "distance_km": float(order.distance_km) if order.distance_km is not None else None,
        # Phase 2: fixed-fare fields.
        "fare_mode": order.fare_mode.value if order.fare_mode else None,
        "fixed_offer_id": str(order.fixed_offer_id) if order.fixed_offer_id else None,
        "driver_price_hkd": money_str(order.driver_price_hkd)
        if order.driver_price_hkd is not None
        else None,
        "platform_fee_hkd": money_str(order.platform_fee_hkd)
        if order.platform_fee_hkd is not None
        else None,
        "passenger_price_hkd": money_str(order.passenger_price_hkd)
        if order.passenger_price_hkd is not None
        else None,
        # P4 in-trip lifecycle. Every field is optional, so a client that ignores
        # them sees exactly the payload it saw before — the addition is
        # backward-compatible by construction.
        "started_at": order.started_at.isoformat() if order.started_at else None,
        "arrival_confirmed_at": (
            order.arrival_confirmed_at.isoformat() if order.arrival_confirmed_at else None
        ),
        "destination_change_count": order.destination_change_count or 0,
        "interruption_reason": (
            order.interruption_reason.value if order.interruption_reason else None
        ),
        "interrupted_at": order.interrupted_at.isoformat() if order.interrupted_at else None,
        "interrupted_by_kind": (
            order.interrupted_by_kind.value if order.interrupted_by_kind else None
        ),
    }

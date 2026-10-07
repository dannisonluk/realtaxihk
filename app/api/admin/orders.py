"""Order monitoring. `/orders*`. Read-only, readable by every role.

The console had no order view at all: `app/api/orders.py` is entirely the
passenger and driver view, so when a passenger called to say their driver never
arrived, there was no way to answer "what state is that trip in, who is the
driver, and how long ago did they accept". Every field here already existed on
`orders` — nothing new is measured, it is only surfaced.

No money and no state change, so no `require_role` beyond `require_admin`.
SUPPORT is exactly who takes that phone call."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._shared import _ledger_out
from app.api.schemas import AdminOrderDetailOut, AdminOrderPageOut, ReceiptOut
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.money import meter_str, money_str
from app.models import LedgerEntry, Order, OrderEvent, OrderEventType, OrderFareMode, OrderStatus
from app.services.receipt.receipt_service import render_receipt_text

router = APIRouter()

_ORDER_OPEN_STATUSES = (
    OrderStatus.CREATED,
    OrderStatus.BROADCASTING,
    OrderStatus.ACCEPTED,
    OrderStatus.PENDING_ARRIVAL_CONFIRM,
    OrderStatus.DRIVER_ARRIVED,
    OrderStatus.IN_TRIP,
    OrderStatus.DESTINATION_CHANGED,
)


def _order_timeline(order: Order) -> list[dict]:
    """The four timestamps in order, with server-computed deltas.

    `elapsed_seconds` is computed here rather than in the client because the
    client would subtract two ISO strings and get it wrong across a DST
    boundary — Hong Kong has no DST today, but a server-side subtraction is
    correct regardless and costs nothing.

    A step whose timestamp is absent is emitted with `at: null` rather than
    omitted, so the console renders a fixed-length timeline and a missing step
    is visibly *missing* instead of silently shorter.
    """
    steps = [
        ("created", order.created_at),
        ("accepted", order.accepted_at),
        ("driver_arrived", order.driver_arrived_at),
        ("completed", order.completed_at),
        ("cancelled", order.cancelled_at),
    ]
    first = order.created_at
    out = []
    for label, at in steps:
        elapsed = None
        if at is not None and first is not None:
            elapsed = int((at - first).total_seconds())
        out.append(
            {
                "step": label,
                "at": at.isoformat() if at else None,
                "elapsed_seconds": elapsed,
            }
        )
    return out


def _unsettled_penalty(
    events: Sequence[OrderEvent], cancellation_reason: str | None
) -> dict | None:
    """The passenger-side penalty that was recorded but not debited.

    The passenger has no wallet, so the P4 cancellation handler writes
    `PENALTY_CHARGED` with `settled: false` instead of a ledger row.  This is
    the record an operator needs for an offline collection decision; it stays
    out of the list row because it is per-order event JSON.
    """
    for event in reversed(events):
        payload = event.payload or {}
        if payload.get("settled") is False and payload.get("amount_hkd"):
            return {
                "amount_hkd": str(payload["amount_hkd"]),
                "basis_hkd": str(payload.get("basis_hkd") or "0.00"),
                "share_percent": str(payload.get("share_percent") or "100"),
                "reason_code": payload.get("reason_code"),
                "cancellation_reason": cancellation_reason,
                "actor_kind": event.actor_kind,
                "charged_at": event.created_at.isoformat() if event.created_at else None,
            }
    return None


def _admin_order_out(order: Order) -> dict:
    return {
        "id": str(order.id),
        "status": order.status.value,
        "taxi_type": order.taxi_type,
        "fare_mode": order.fare_mode.value,
        "passenger_id": str(order.passenger_id),
        "driver_id": str(order.driver_id) if order.driver_id else None,
        "pickup_address": order.pickup_address,
        "dropoff_address": order.dropoff_address,
        "distance_km": meter_str(Decimal(order.distance_km)),
        "estimated_total_hkd": money_str(Decimal(order.estimated_total_hkd)),
        "discount_percent": money_str(Decimal(order.discount_percent or 0)),
        "accepted_at": order.accepted_at.isoformat() if order.accepted_at else None,
        "driver_arrived_at": (
            order.driver_arrived_at.isoformat() if order.driver_arrived_at else None
        ),
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancellation_reason": order.cancellation_reason,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


def _admin_order_detail_out(order: Order) -> dict:
    """The dispute-relevant additions to a single order.

    Split out of `_admin_order_out` because these ride the detail page only:
    the list is a fixed-column table and serialising a requirements/receipt
    JSONB blob for every row of every scroll is a cost with no reader.

    Everything here is the **frozen** record of what the passenger asked for,
    captured at booking time. A dispute is usually about exactly this — "I
    asked for a silent car and he had the radio on", "I asked to pay by
    Octopus and he wanted cash", "I asked for a receipt and never got one" —
    and the operator's job is to check the request as recorded, not to
    arbitrate between two recollections on the phone.

    `requirements` is `None` when nothing special was asked for; the write
    path stores `None` rather than an object of all-false flags so "never
    mentioned a pet" stays distinguishable from "said no pet".
    """
    return {
        "requirements": order.requirements_json,
        # Both are stored as `{"methods": [...]}`; unwrap so the console does
        # not have to know the envelope. Same shape `order_out` returns.
        "payment_preference": (order.payment_preference_json or {}).get("methods", []),
        "driver_payment_methods": (order.driver_payment_methods_json or {}).get("methods", []),
        "premium_destination": order.premium_destination_json,
        "destination_area": order.destination_area,
        "pickup_area": order.pickup_area,
        "receipt_requested": bool(order.receipt_requested),
        "receipt_requested_at": (
            order.receipt_requested_at.isoformat() if order.receipt_requested_at else None
        ),
    }


@router.get("/orders", response_model=AdminOrderPageOut)
async def list_orders(
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    fare_mode: Annotated[str | None, Query(max_length=8)] = None,
    driver_id: uuid.UUID | None = None,
    passenger_id: uuid.UUID | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Order table, newest first, filterable the ways an operator actually asks.

    `open_only` exists because the most common question is not "show me
    cancelled trips" but "show me everything still moving" — the SLA page and
    the live-ops count both want it, and expressing it as five OR'd status
    values belongs on the server where the list of "open" statuses is defined
    next to the enum.

    `status` is validated against `OrderStatus` and a bad value is a 400: a
    filter that silently matches nothing is how an operator concludes there
    are no cancelled trips this week.

    `fare_mode` is validated the same way, and for the same reason. It answers
    the audit question the fixed-fare feature created: "show me every trip
    where a price was agreed up front", which is the population a fee-differential
    or overcharging review has to start from.

    `since`/`until` are half-open on `created_at`, matching `/audit`.
    """
    q = select(Order).order_by(Order.created_at.desc())
    count_q = select(func.count()).select_from(Order)
    filters = []
    if status_filter:
        try:
            filters.append(Order.status == OrderStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "unknown order status",
                    "reason": "UNKNOWN_STATUS",
                    "allowed": [s.value for s in OrderStatus],
                },
            ) from None
    if open_only:
        filters.append(Order.status.in_(_ORDER_OPEN_STATUSES))
    if fare_mode:
        try:
            filters.append(Order.fare_mode == OrderFareMode(fare_mode))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "unknown fare mode",
                    "reason": "UNKNOWN_FARE_MODE",
                    "allowed": [m.value for m in OrderFareMode],
                },
            ) from None
    if driver_id is not None:
        filters.append(Order.driver_id == driver_id)
    if passenger_id is not None:
        filters.append(Order.passenger_id == passenger_id)
    if since is not None:
        filters.append(Order.created_at >= since)
    if until is not None:
        filters.append(Order.created_at < until)
    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_admin_order_out(o) for o in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/orders/{order_id}", response_model=AdminOrderDetailOut)
async def order_detail(
    order_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """One trip, in full.

    `fare` is the stored `fare_json` snapshot, not a recomputation. Recomputing
    would produce today's number for a trip quoted under an older tariff — and
    the disputed amount is always the one the passenger was actually quoted,
    which is also why `tariff_version` is returned beside it.
    """
    order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    penalty_events = (
        (
            await session.execute(
                select(OrderEvent)
                .where(
                    OrderEvent.order_id == order_id,
                    OrderEvent.event == OrderEventType.PENALTY_CHARGED,
                )
                .order_by(OrderEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    ledger_rows = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.order_id == order_id)
                .order_by(LedgerEntry.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return {
        **_admin_order_out(order),
        **_admin_order_detail_out(order),
        "tariff_version": order.tariff_version,
        "fare": order.fare_json,
        "broadcast_radius_km": meter_str(Decimal(order.broadcast_radius_km)),
        "timeline": _order_timeline(order),
        "unsettled_penalty": _unsettled_penalty(penalty_events, order.cancellation_reason),
        "ledger": {"items": [_ledger_out(e) for e in ledger_rows]},
    }


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut)
async def order_receipt(
    order_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The frozen receipt, read-only for operators.

    The party-facing `GET /orders/{id}/receipt` necessarily *issues* a receipt
    when one has not been frozen, so that the document a client downloads and
    the document the passenger requested are the same bytes. An operator
    browsing this page must not mutate an order: if the passenger never
    requested a receipt, this answers 404 rather than creating one.
    """
    order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    snapshot = order.receipt_snapshot_json
    if not snapshot:
        raise HTTPException(status_code=404, detail="receipt not issued")
    return {**snapshot, "text": render_receipt_text(snapshot)}

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
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._shared import _ledger_out
from app.api.schemas import AdminOrderDetailOut, AdminOrderPageOut
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.money import meter_str, money_str
from app.models import LedgerEntry, Order, OrderStatus

router = APIRouter()

_ORDER_OPEN_STATUSES = (
    OrderStatus.CREATED,
    OrderStatus.BROADCASTING,
    OrderStatus.ACCEPTED,
    OrderStatus.DRIVER_ARRIVED,
    OrderStatus.IN_TRIP,
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


def _admin_order_out(order: Order) -> dict:
    return {
        "id": str(order.id),
        "status": order.status.value,
        "taxi_type": order.taxi_type,
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


@router.get("/orders", response_model=AdminOrderPageOut)
async def list_orders(
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
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
        "tariff_version": order.tariff_version,
        "fare": order.fare_json,
        "broadcast_radius_km": meter_str(Decimal(order.broadcast_radius_km)),
        "timeline": _order_timeline(order),
        "ledger": {"items": [_ledger_out(e) for e in ledger_rows]},
    }

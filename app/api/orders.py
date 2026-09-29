"""Order API: create (fare snapshot), grab, lifecycle, cancel, detail, history.

Hardening wave (docs/PRODUCTION_READINESS.md):
- P0-3 all user-facing routes run require_active_user (deactivation is live);
- B3  tip capped (Numeric(10,2) overflow guard);
- P1-10 order snapshots now carry route tolls (tunnels/crosses_harbour);
- P2-1 GET /{order_id} (participants) + GET "" history (passenger/driver view);
- P2-10 nearby degrades to an empty page when Redis is down (fail-open
  dispatch), never 500s the driver's map.

Security wave (docs/SECURITY_AUDIT.md):
- SEC-09 `tunnels` is capped at the 8 real tunnel values. Unbounded, it was both
  a response-amplification vector (100k invalid entries echoed back as 32.8 MB)
  and a storage/egress one (a 200k-entry order wrote 3.4 MB of fare_json that
  every subsequent list call re-sent);
- SEC-26 the `before_id` keyset cursor is scoped to the caller's own orders, so
  it can no longer be used to probe whether an arbitrary order id exists.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, get_session_factory
from app.core.deps import Principal, require_active_user
from app.core.exceptions import BusinessRuleError
from app.models import (
    DriverProfile,
    DriverStatus,
    LedgerEntryType,
    Order,
    OrderStatus,
    UserRole,
)
from app.services.geo_service import GeoService
from app.services.grab_service import GrabService
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderService, order_out
from app.services.state_machine import assert_order_transition

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

_ORDER_RATE_LIMIT = 5  # per 60s per passenger
_ORDER_WINDOW_S = 60
# There are exactly 8 tunnels in the fare engine's Tunnel enum; anything beyond
# that is either a mistake or an attack.
_MAX_TUNNELS = 8


def _redis(request: Request):
    return request.app.state.redis_factory()


class OrderCreateIn(BaseModel):
    pickup_lat: float = Field(ge=22.1, le=22.6)
    pickup_lng: float = Field(ge=113.8, le=114.5)
    dropoff_lat: float = Field(ge=22.1, le=22.6)
    dropoff_lng: float = Field(ge=113.8, le=114.5)
    pickup_address: str = Field(min_length=3, max_length=255)
    dropoff_address: str = Field(min_length=3, max_length=255)
    distance_km: Decimal = Field(gt=0, le=100)
    waiting_min: Decimal = Field(default=Decimal("0"), ge=0, le=600)
    taxi_type: str = Field(pattern=r"^(URBAN|NT|LANTAU)$")
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tip: Decimal = Field(default=Decimal("0"), ge=0, le=10000)  # B3: Numeric(10,2) guard
    # P1-10: route tolls join the snapshot (previously hard-coded empty).
    # SEC-09: bounded — see _MAX_TUNNELS.
    tunnels: list[str] = Field(default_factory=list, max_length=_MAX_TUNNELS)
    crosses_harbour: bool = False
    pickup_at_cross_harbour_stand: bool = False

    @field_validator("tunnels", mode="before")
    @classmethod
    def cap_tunnels(cls, v):
        """Reject an over-long tunnel list before item validation (SEC-09).

        See the identical guard in `app/api/fare.py`: a `before` validator turns an
        oversized request into a single small error instead of one error object per
        element.
        """
        if isinstance(v, (list, tuple)) and len(v) > _MAX_TUNNELS:
            raise ValueError(f"at most {_MAX_TUNNELS} tunnels may be listed")
        return v

    @field_validator("distance_km", "waiting_min", "discount_percent", "tip")
    @classmethod
    def finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("must be a finite number")
        return v


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)


async def _get_order(session: AsyncSession, order_id: str) -> Order:
    from uuid import UUID

    try:
        oid = UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
    order = await session.get(Order, oid)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


async def _driver_profile_of(session: AsyncSession, user_id) -> DriverProfile | None:
    return (
        (await session.execute(select(DriverProfile).where(DriverProfile.user_id == user_id)))
        .scalars()
        .first()
    )


@router.post("", status_code=201)
async def create_order(
    payload: OrderCreateIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(f"order:create:{user.id}", _ORDER_RATE_LIMIT, _ORDER_WINDOW_S):
        raise HTTPException(status_code=429, detail="too many orders, slow down")
    try:
        order = await OrderService(session).create(user.id, payload)
    except BusinessRuleError:
        # Already carries machine-readable `details`; `BusinessRuleError`
        # subclasses `ValueError`, so the branch below would discard them.
        raise
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    await GeoService(request.app.state.redis_factory()).index_order(
        str(order.id), payload.pickup_lat, payload.pickup_lng
    )
    return order_out(order)


@router.get("/nearby")
async def nearby_orders(
    request: Request,
    lat: Annotated[float, Query(ge=22.1, le=22.6)],
    lng: Annotated[float, Query(ge=113.8, le=114.5)],
    radius_km: Annotated[float, Query(ge=0.5, le=10)] = 3.0,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    import logging

    try:
        ids = await GeoService(request.app.state.redis_factory()).nearby_order_ids(
            lat, lng, radius_km
        )
    except Exception:  # P2-10: Redis down -> fail-open dispatch, not a 500
        logging.getLogger("realtaxihk.orders").exception("nearby: geo index unavailable")
        return {"items": [], "degraded": True}
    if not ids:
        return {"items": []}
    orders = (
        (
            await session.execute(
                select(Order)
                .where(Order.id.in_([uuid.UUID(i) for i in ids]))
                .where(Order.status == OrderStatus.BROADCASTING)
            )
        )
        .scalars()
        .all()
    )
    by_id = {str(o.id): o for o in orders}
    return {"items": [order_out(by_id[i]) for i in ids if i in by_id]}


@router.get("")
async def my_orders(
    role: Annotated[str, Query(pattern=r"^(passenger|driver)$")] = "passenger",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before_id: uuid.UUID | None = Query(default=None),
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P2-1: order history (newest first, keyset via before_id).

    SEC-26: the cursor is resolved inside the caller's own scope. Looking the
    anchor up by id alone turned `before_id` into an existence/timestamp oracle
    for arbitrary orders belonging to other users.
    """
    q = select(Order).order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
    if role == "driver":
        profile = await _driver_profile_of(session, user.id)
        if profile is None:
            return {"items": []}
        scope = Order.driver_id == profile.id
    else:
        scope = Order.passenger_id == user.id
    q = q.where(scope)

    if before_id is not None:
        anchor = (
            await session.execute(
                select(Order.created_at, Order.id).where(Order.id == before_id, scope)
            )
        ).first()
        if anchor is None:
            # Not the caller's order (or nonexistent) — indistinguishable by design.
            raise HTTPException(status_code=404, detail="cursor not found")
        q = q.where((Order.created_at, Order.id) < (anchor[0], anchor[1]))

    rows = (await session.execute(q)).scalars().all()
    return {"items": [order_out(o) for o in rows]}


@router.get("/{order_id}")
async def order_detail(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P2-1: participants (or admin) can read one order."""
    order = await _get_order(session, order_id)
    profile = await _driver_profile_of(session, user.id)
    is_party = order.passenger_id == user.id or (
        profile is not None and order.driver_id == profile.id
    )
    if not is_party and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="not a party of this order")
    return order_out(order)


@router.post("/{order_id}/grab")
async def grab_order(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
    factory=Depends(get_session_factory),
    redis=Depends(_redis),
):
    profile = await _driver_profile_of(session, user.id)
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="only ACTIVE drivers can grab orders")

    order = await _get_order(session, order_id)  # existence pre-check
    grab = GrabService(redis, factory)
    won = await grab.grab(order_id=str(order.id), driver_user_id=str(user.id))
    if not won:
        raise HTTPException(status_code=409, detail="order was taken by another driver or is gone")
    await session.refresh(order)  # grab service committed in its own session
    return order_out(order)


async def _assigned_driver_guard(
    session: AsyncSession, order: Order, user: Principal
) -> DriverProfile:
    profile = await _driver_profile_of(session, user.id)
    if profile is None or order.driver_id is None or order.driver_id != profile.id:
        raise HTTPException(status_code=403, detail="not the assigned driver")
    return profile


@router.post("/{order_id}/arrive")
async def order_arrive(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.DRIVER_ARRIVED)
    return order_out(order)


@router.post("/{order_id}/start")
async def order_start(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.IN_TRIP)
    return order_out(order)


@router.post("/{order_id}/complete")
async def order_complete(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.COMPLETED)
    return order_out(order)


@router.post("/{order_id}/cancel")
async def order_cancel(
    order_id: str,
    payload: CancelIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    profile = await _driver_profile_of(session, user.id)
    is_passenger = order.passenger_id == user.id
    is_assigned_driver = profile is not None and order.driver_id == profile.id
    if not (is_passenger or is_assigned_driver):
        raise HTTPException(status_code=403, detail="not a party of this order")

    # legality first — never charge a penalty for an impossible transition
    assert_order_transition(order.status, OrderStatus.CANCELLED)

    if order.status == OrderStatus.BROADCASTING:
        await GeoService(request.app.state.redis_factory()).remove_order(str(order.id))

    if is_assigned_driver and order.status in (
        OrderStatus.ACCEPTED,
        OrderStatus.DRIVER_ARRIVED,
    ):
        from app.core.config import get_settings

        penalty = Decimal(get_settings().no_show_penalty_hkd)
        await LedgerService(session).append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.PENALTY_DEDUCTION,
            amount_hkd=-penalty,
            note=f"driver cancellation after acceptance: {payload.reason}"[:200],
            order_id=order.id,
            created_by=user.id,
        )

    await OrderService(session).transition(order, OrderStatus.CANCELLED)
    return order_out(order)

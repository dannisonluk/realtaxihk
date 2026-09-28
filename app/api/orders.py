"""Order API: create (fare snapshot), grab, lifecycle transitions, cancel."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_redis, get_session, get_session_factory
from app.core.deps import Principal, get_current_user
from app.core.rate_limit import RateLimiter
from app.models import (
    DriverProfile,
    DriverStatus,
    LedgerEntryType,
    Order,
    OrderStatus,
)
from app.services.geo_service import GeoService
from app.services.grab_service import GrabService
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderService, order_out
from app.services.state_machine import assert_order_transition

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

_ORDER_RATE_LIMIT = 5  # per 60s per passenger
_ORDER_WINDOW_S = 60


def _rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


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
    tip: Decimal = Field(default=Decimal("0"), ge=0)

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
        await session.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
    ).scalars().first()


@router.post("", status_code=201)
async def create_order(
    payload: OrderCreateIn,
    request: Request,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    rds=Depends(_redis),
):
    limiter: RateLimiter = request.app.state.rate_limiter
    if not await limiter.allow(f"order:create:{user.id}", _ORDER_RATE_LIMIT, _ORDER_WINDOW_S):
        raise HTTPException(status_code=429, detail="too many orders, slow down")
    order = await OrderService(session).create(user.id, payload)
    await GeoService(rds).index_order(str(order.id), payload.pickup_lat, payload.pickup_lng)
    return order_out(order)


@router.get("/nearby")
async def nearby_orders(
    lat: Annotated[float, Query(ge=22.1, le=22.6)],
    lng: Annotated[float, Query(ge=113.8, le=114.5)],
    radius_km: Annotated[float, Query(ge=0.5, le=10)] = 3.0,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    rds=Depends(_redis),
):
    ids = await GeoService(rds).nearby_order_ids(lat, lng, radius_km)
    if not ids:
        return {"items": []}
    orders = (
        await session.execute(
            select(Order)
            .where(Order.id.in_([uuid.UUID(i) for i in ids]))
            .where(Order.status == OrderStatus.BROADCASTING)
        )
    ).scalars().all()
    by_id = {str(o.id): o for o in orders}
    return {"items": [order_out(by_id[i]) for i in ids if i in by_id]}


@router.post("/{order_id}/grab")
async def grab_order(
    order_id: str,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    factory=Depends(get_session_factory),
    redis=Depends(get_redis),
):
    profile = await _driver_profile_of(session, user.id)
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="only ACTIVE drivers can grab orders")

    order = await _get_order(session, order_id)  # existence pre-check
    grab = GrabService(redis, factory)
    won = await grab.grab(order_id=str(order.id), driver_user_id=str(user.id))
    if not won:
        raise HTTPException(
            status_code=409, detail="order was taken by another driver or is gone"
        )
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
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.DRIVER_ARRIVED)
    return order_out(order)


@router.post("/{order_id}/start")
async def order_start(
    order_id: str,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.IN_TRIP)
    return order_out(order)


@router.post("/{order_id}/complete")
async def order_complete(
    order_id: str,
    user: Principal = Depends(get_current_user),
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
    user: Principal = Depends(get_current_user),
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

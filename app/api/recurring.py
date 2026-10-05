"""Recurring ride passenger API.

A recurring ride is a standing template. Creating one never mints an order
immediately; the background minter creates the order when `next_run_at` comes
due. Every order still freezes its own fare snapshot at creation time.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.orders import OrderCreateIn
from app.api.schemas import RecurringRideListOut, RecurringRideOut
from app.core.db import get_session
from app.core.deps import require_phone_current
from app.models import RecurringRide, RecurringStatus, User
from app.services.recurring.recurring_service import (
    RecurringAlreadyExists,
    RecurringService,
)

router = APIRouter(prefix="/api/v1/recurring-rides", tags=["recurring"])


class RecurringRideCreateIn(BaseModel):
    """Create a weekly ride template from an order payload.

    The same OrderCreateIn shape is used as for POST /orders; validation,
    fare calculation and snapshot freezing all happen through the normal
    OrderService path. Passenger identity comes from the authenticated token.
    """

    weekday: int = Field(ge=1, le=7, description="1=Monday .. 7=Sunday")
    scheduled_time: str = Field(pattern=r"^\d{2}:\d{2}$", description="HH:MM in Asia/Hong_Kong")
    source_order_id: uuid.UUID | None = None
    order: OrderCreateIn


class RecurringRideUpdateIn(BaseModel):
    status: RecurringStatus = Field(description="ACTIVE, PAUSED, or CANCELLED")


def _out(ride: RecurringRide) -> RecurringRideOut:
    return RecurringRideOut.model_validate(ride)


@router.post("", response_model=RecurringRideOut, status_code=status.HTTP_201_CREATED)
async def create_recurring_ride(
    payload: RecurringRideCreateIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_phone_current),
) -> RecurringRideOut:
    try:
        ride = await RecurringService(session).create(
            current_user.id,
            weekday=payload.weekday,
            scheduled_time=payload.scheduled_time,
            template=payload.order.model_dump(mode="json"),
            source_order_id=payload.source_order_id,
        )
    except RecurringAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _out(ride)


@router.get("", response_model=RecurringRideListOut)
async def list_recurring_rides(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_phone_current),
) -> RecurringRideListOut:
    rides = await RecurringService(session).list_for_passenger(current_user.id)
    return RecurringRideListOut(items=[_out(r) for r in rides])


@router.patch("/{ride_id}", response_model=RecurringRideOut)
async def update_recurring_ride(
    ride_id: uuid.UUID,
    payload: RecurringRideUpdateIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_phone_current),
) -> RecurringRideOut:
    ride = await RecurringService(session).get_for_passenger(ride_id, current_user.id)
    if ride is None:
        raise HTTPException(status_code=404, detail="recurring ride not found")
    try:
        await RecurringService(session).set_status(ride, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return _out(ride)

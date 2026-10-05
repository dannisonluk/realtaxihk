"""Recurring ride response schemas (response models only — see audit rules).

Request payloads for recurring rides live in `app/api/recurring.py`, following
the same convention as `OrderCreateIn` in `app/api/orders.py`: request bodies
stay in the API module, `app.api.schemas` exports response models only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RecurringRideOut(BaseModel):
    """A passenger's recurring ride template as returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    frequency: str
    weekday: int
    scheduled_time: str
    next_run_at: datetime
    source_order_id: UUID | None = None
    last_order_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class RecurringRideListOut(BaseModel):
    items: list[RecurringRideOut]


__all__ = ["RecurringRideListOut", "RecurringRideOut"]

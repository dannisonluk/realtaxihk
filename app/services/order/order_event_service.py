"""Recording order events — the one writer for `order_events`.

A helper rather than inline `session.add(OrderEvent(...))` at every call site,
for the same reason `LedgerService.append` exists: the timeline is evidence, and
evidence written five different ways drifts into five different shapes. One
function means `payload` keys are consistent, `actor_kind` uses one vocabulary,
and a missing field is a missing argument rather than a forgotten line.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrderEvent, OrderEventType


async def record_order_event(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    event: OrderEventType,
    from_status: str | None = None,
    to_status: str | None = None,
    actor_kind: str | None = None,
    actor_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> OrderEvent:
    """Append one line to an order's timeline.

    Flushes so the row exists within the caller's transaction — the caller owns
    the commit, so an event can never be committed without the mutation it
    records, nor the mutation without its event.
    """
    row = OrderEvent(
        order_id=order_id,
        event=event,
        from_status=from_status,
        to_status=to_status,
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row

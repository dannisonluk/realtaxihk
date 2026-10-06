"""P4 §7 — announce trip lifecycle events on the order's WebSocket channel.

`TripHub` was built for exactly this and then never called: `publish()` was
reachable only from `record_tick`, so a passenger's socket carried positions and
no lifecycle news. A grab, an arrival claim or an interruption therefore reached
the other party only on the next poll (10 s, `AppConfig.locationPollInterval`).

`docs/IN_TRIP_REDESIGN.md` §7 turns that from a harmless gap into a blocking one:
the arrival-confirmation prompt has to appear *when the driver claims arrival*,
and a ten-second delay is the wrong behaviour in precisely the case the whole
two-step arrival flow exists for.

Two rules live here rather than at each call site:

1. **Commit before publishing.** Subscribers react to the event by re-reading the
   order. `get_session` commits *after* the response is generated, so a publish
   issued from inside a handler would race its own transaction and every
   listener would read the row from *before* the change — the staleness this
   module exists to remove. Routing every call through here makes the ordering
   impossible to get wrong.

   Consequence worth stating: on a `Depends(get_session)` route the request
   commits twice — once here, once in the dependency after the response. The
   second one is a no-op (nothing is pending, and the session factory sets
   `expire_on_commit=False`), and it is the price of publishing early. A route
   that wants a single commit point must not also commit by hand: the first
   commit would make the change durable while a failure in the second still
   returned a 500, and the client's retry would then reach state that had
   already moved. Dispute resolution previously had exactly that bug.

2. **Delivery is best-effort.** By the time we publish, the state change is
   already durable. A Redis blip must not turn a completed arrival confirmation
   into a 500 that the client will retry, so publish failures are logged and
   swallowed. The commit above is deliberately *not* covered by that: a caller
   whose write did not land has to hear about it.

The channel name comes from `trip_service.channel_for` — the same function the
hub uses. Restating the literal here is how this codebase previously grew three
copies of the dispatch GEO key (see `geo_service.geo_orders_key`).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_redis
from app.models import Order
from app.services.order.trip_service import channel_for

logger = logging.getLogger("realtaxihk.trip_events")

#: Message `type` for a lifecycle event. The mobile client decodes unknown types
#: to `TripUnknownEvent` rather than throwing, so an older build ignores these
#: instead of breaking — see `mobile/lib/models/trip.dart`, which decodes
#: `type: "order"` into `TripOrderEvent` and re-reads the order on either screen.
LIFECYCLE_TYPE = "order"


async def publish_lifecycle(
    session: AsyncSession,
    order: Order,
    event: str,
    **extra: object,
) -> None:
    """Commit the caller's work, then announce `event` on the order's channel.

    Raises only if the **commit** fails — the caller must learn that its write
    did not land. A failure to publish is logged and swallowed: see rule 2.

    Reading `order.status` after the commit is safe because the session factory
    sets `expire_on_commit=False` (`core/db.py`). Switching that to `True` would
    make this line raise before the publish; keep the two in step.
    """
    await session.commit()
    message: dict[str, object] = {
        "type": LIFECYCLE_TYPE,
        "event": event,
        "order_id": str(order.id),
        "status": order.status.value,
        **extra,
    }
    try:
        await get_redis().publish(channel_for(str(order.id)), json.dumps(message))
    except Exception:
        logger.exception("trip lifecycle publish failed order_id=%s event=%s", order.id, event)

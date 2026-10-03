"""Atomic order grabbing — the database is the arbiter, Redis is an optimisation.

Winner:   Redis SETNX lock -> conditional UPDATE ... WHERE status='BROADCASTING'
          -> rowcount 1 -> commit -> release lock.
Losers:   SETNX fails, or the conditional UPDATE matches no row -> return False
          (API maps to 409 CONFLICT).
Crashes:  lock TTL (15s) auto-expires; release is token-checked via Lua so a
          slow winner never deletes a successor's lock.

SEC-06: the Redis lock key is `lock:grab:{order_id}` — fully predictable from the
order id, so anyone able to reach Redis could hold it forever and make the order
ungrabbable (the live check proved it: `SET lock:grab:<id> attacker NX PX 15000`
succeeded). The exposure itself is fixed at the infrastructure layer (Redis is no
longer published to 0.0.0.0 and now requires a password), but a predictable key
must not be the only thing preventing a double assignment. The conditional UPDATE
below makes correctness independent of Redis: if two drivers ever slip past the
lock, the second UPDATE matches zero rows and loses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.models import DriverProfile, DriverStatus, Order, OrderStatus
from app.services.order.state_machine import assert_order_transition

_LOCK_TTL_MS = 15_000

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class GrabService:
    def __init__(self, redis, session_factory):
        self.redis = redis
        self.session_factory = session_factory

    async def grab(self, order_id: str, driver_user_id: str) -> bool:
        """Return True if this caller won the order, False if it lost the race.

        Every failure below returns `False` rather than raising, and the API
        collapses them all into one 409. That is intentional: from the driver's
        point of view "someone else took it", "it was cancelled a moment ago"
        and "it never existed" are the same event — the order is not available.
        Distinguishing them would leak whether an arbitrary order id exists,
        and the `existence pre-check` in the route already answers that for
        orders the caller is allowed to see.
        """
        lock_key = f"lock:grab:{order_id}"
        lock_token = uuid.uuid4().hex
        acquired = await self.redis.set(lock_key, lock_token, nx=True, px=_LOCK_TTL_MS)
        if not acquired:
            return False
        try:
            async with self.session_factory() as session:
                order = await session.get(Order, uuid.UUID(order_id))
                profile = (
                    (
                        await session.execute(
                            select(DriverProfile).where(
                                DriverProfile.user_id == uuid.UUID(driver_user_id)
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if (
                    order is None
                    or order.status != OrderStatus.BROADCASTING
                    or profile is None
                    or profile.status != DriverStatus.ACTIVE
                ):
                    return False
                assert_order_transition(order.status, OrderStatus.ACCEPTED)
                # Conditional UPDATE: only a BROADCASTING row can be claimed, so
                # the database itself enforces single-assignment.
                result = await session.execute(
                    update(Order)
                    .where(Order.id == order.id, Order.status == OrderStatus.BROADCASTING)
                    .values(
                        status=OrderStatus.ACCEPTED,
                        driver_id=profile.id,
                        accepted_at=datetime.now(UTC),
                    )
                )
                if (result.rowcount or 0) != 1:
                    await session.rollback()
                    return False
                await session.commit()
            # Index removal is deliberately *after* the commit and outside the
            # try/finally's failure path: if the commit succeeded the order is
            # genuinely taken, and a stale geo member is harmless (the `nearby`
            # query re-filters on `status == BROADCASTING`, so a lingering id
            # simply fails to resolve). Doing this before the commit would risk
            # removing a still-available order from the dispatch index.
            await self.redis.zrem("geo:orders:active", str(order_id))
        finally:
            # Lock always released; a DB failure propagates (order stays BROADCASTING).
            await self.redis.eval(_RELEASE_LUA, 1, lock_key, lock_token)
        return True

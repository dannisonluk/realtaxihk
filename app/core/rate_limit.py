"""Redis rate limiter — fixed-window INCR, atomic check+increment.

Two entry points:
- `count(key, window_s)` increments and returns the new count. Use it when the
  counter is a *signal* (alerting, degradation) rather than a gate.
- `allow(key, limit, window_s)` is the gate: True while under the limit.

SEC-08 exists because the only global OTP cap was a gate on a key shared by the
whole platform — 500 requests from one attacker locked out every user. Callers
that need a platform-wide ceiling should use `count()` and degrade, not `allow()`.
"""

from __future__ import annotations

import contextlib
import time


class RateLimiter:
    def __init__(self, redis, namespace: str = ""):
        self.redis = redis
        self.namespace = namespace

    async def count(self, key: str, window_s: int) -> int:
        """Increment the window counter and return the new value."""
        window = int(time.time()) // window_s
        rkey = f"rl:{self.namespace}{key}:{window}"
        count = await self.redis.incr(rkey)
        if count == 1:
            await self.redis.expire(rkey, window_s + 5)
        return int(count)

    async def allow(self, key: str, limit: int, window_s: int) -> bool:
        return await self.count(key, window_s) <= limit

    async def aclose(self) -> None:
        """Release the pooled client on application shutdown.

        The limiter holds one client for the app's lifetime. Without an explicit
        close the connection is torn down when the event loop dies, which
        surfaces as an asyncio `ERROR ... unexpected connection_lost()` line on
        every otherwise-clean shutdown — alarming, and it hides real errors.
        """
        with contextlib.suppress(Exception):
            await self.redis.aclose()

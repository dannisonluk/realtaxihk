"""Redis rate limiter — fixed-window INCR, atomic check+increment.

Two entry points:
- `count(key, window_s)` increments and returns the new count. Use it when the
  counter is a *signal* (alerting, degradation) rather than a gate.
- `allow(key, limit, window_s)` is the gate: True while the request is at or
  under the limit (the configured limit itself is allowed).

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
        """Increment the window counter and return the new value.

        Fixed-window, not sliding. The window key embeds `time // window_s`, so
        the counter rotates on a wall-clock boundary rather than on first use.
        A caller can therefore get up to `2 * limit` requests across a boundary
        (limit at the end of one window, limit at the start of the next). That
        is accepted here: every use is a coarse abuse brake, not a quota, and a
        sliding window would cost a sorted set per key on the hot path.

        The `expire` is only set when the counter is created (`count == 1`), and
        the extra 5s covers clock skew between the app and Redis. It is not set
        on every call on purpose — re-`EXPIRE`-ing would keep pushing the TTL
        out and turn a rotating window into a never-expiring key.
        """
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

"""Redis rate limiter — fixed-window INCR, atomic check+increment."""

from __future__ import annotations

import contextlib
import time


class RateLimiter:
    def __init__(self, redis, namespace: str = ""):
        self.redis = redis
        self.namespace = namespace

    async def allow(self, key: str, limit: int, window_s: int) -> bool:
        window = int(time.time()) // window_s
        rkey = f"rl:{self.namespace}{key}:{window}"
        count = await self.redis.incr(rkey)
        if count == 1:
            await self.redis.expire(rkey, window_s + 5)
        return count <= limit

    async def aclose(self) -> None:
        """Release the pooled client on application shutdown.

        The limiter holds one client for the app's lifetime. Without an explicit
        close the connection is torn down when the event loop dies, which
        surfaces as an asyncio `ERROR ... unexpected connection_lost()` line on
        every otherwise-clean shutdown — alarming, and it hides real errors.
        """
        with contextlib.suppress(Exception):
            await self.redis.aclose()

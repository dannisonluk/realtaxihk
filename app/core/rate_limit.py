"""Redis rate limiter — fixed-window INCR, atomic check+increment."""
from __future__ import annotations

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

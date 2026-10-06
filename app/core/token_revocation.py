"""Access-token revocation via a per-user "epoch" (SEC-17, SEC-18).

Stateless JWTs cannot be un-issued, so a logout — or a detected refresh-token
replay — used to leave the access token valid for the rest of its lifetime (up
to 120 minutes; `ACCESS_TOKEN_EXPIRE_MINUTES` is now 15, which bounds it, but
bounding is not the same as revoking).

Design: one Redis key per user holds the timestamp of the last revocation.
A token is dead if it was issued (`iat`) before that timestamp. That is one key
per user regardless of how many tokens are out, it needs no per-token bookkeeping
or cleanup, and it kills every access token of a user at once — which is exactly
what "reuse detected, assume the device is compromised" requires.

Availability trade-off, stated explicitly: if Redis is unreachable we **fail
open** (log an error and accept the token). Failing closed here would turn a
Redis blip into a total authentication outage for the platform — a worse
outcome than a revoked token surviving for the remainder of its now-15-minute
life. The revocation is best-effort by design, and the short expiry is the
backstop.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("realtaxihk.auth.revocation")

_EPOCH_TTL_S = 30 * 24 * 3600  # a refresh-token lifetime is enough


def _epoch_key(user_id: str) -> str:
    return f"auth:epoch:{user_id}"


async def revoke_user_tokens(redis, user_id) -> bool:
    """Invalidate every access token already issued to `user_id`.

    Returns `False` (after logging) when Redis cannot record the epoch, so the
    caller can decide whether the failure deserves its own alert.
    """
    try:
        await redis.set(_epoch_key(str(user_id)), f"{time.time():.6f}", ex=_EPOCH_TTL_S)
    except Exception:
        logger.exception("could not record token revocation epoch for %s", user_id)
        return False
    return True


async def is_token_revoked(redis, user_id, issued_at) -> bool:
    """True when `issued_at` predates the user's last revocation epoch."""
    if issued_at is None:
        return False
    try:
        raw = await redis.get(_epoch_key(str(user_id)))
    except Exception:
        # Fail open — see module docstring. Never break auth on a Redis blip.
        logger.exception("could not read token revocation epoch for %s", user_id)
        return False
    if raw is None:
        return False
    try:
        return float(issued_at) < float(raw)
    except (TypeError, ValueError):
        return False

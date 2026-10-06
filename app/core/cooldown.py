"""Cancellation cool-down — a Redis TTL key, never a database column.

DECISION-5 (`docs/IN_TRIP_REDESIGN.md` §5.2): a party who defaults — cancels
after the driver has already committed, or ends a trip early — is barred from
starting new business for fifteen minutes. The bar is deliberately a *cool-down*
and not a punishment: it exists to break the "default, re-book, default again"
loop, and it must clear itself with no job and no operator.

Two consequences shape the implementation:

* **It lives in Redis with a TTL.** Expiry becomes the store's problem, not a
  sweeper's. A job that clears cool-downs is a job that can fail silently and
  leave people locked out — the worst version of this feature.
* **Every default re-arms the full window.** A second default cannot shorten the
  first one's remaining time.

The key is namespaced like every other Redis key here
(`Settings.redis_key_namespace`), so two test processes — or two app instances
sharing one Redis — never share a cool-down.

The **account id** is the key, not the order or the device: the bar is on the
person, and it has to survive reinstalling the app.
"""

from __future__ import annotations

from app.core.config import get_settings

# Fifteen minutes, per DECISION-5. Named so the router can echo it in
# `Retry-After` without restating the number.
COOLDOWN_SECONDS = 900

# The two parties, lowercase because these strings go into a key. Kept as
# constants so a typo is an import error rather than a key that never matches.
PASSENGER = "passenger"
DRIVER = "driver"


def cooldown_key(party: str, account_id) -> str:
    """`<namespace>cooldown:<party>:<account_id>`.

    Namespaced through `Settings.redis_key_namespace` for the same reason as
    `geo_orders_key()`: a hard-coded prefix made two concurrent test runs share
    state and report each other's cool-downs as their own.
    """
    return f"{get_settings().redis_key_namespace}cooldown:{party}:{account_id}"


async def set_cooldown(redis, party: str, account_id, *, seconds: int = COOLDOWN_SECONDS) -> None:
    """Arm (or re-arm) the cool-down for one party.

    `ex=` on the write, not a separate `expire` — a crash between the two would
    leave a key with no TTL, i.e. a permanent lock.
    """
    await redis.set(cooldown_key(party, account_id), "1", ex=seconds)


async def cooldown_remaining(redis, party: str, account_id) -> int:
    """Seconds left, or 0 when there is no cool-down.

    A Redis error is *not* swallowed into 0: the caller decides. Returning 0 on
    error would fail open and let a defaulting party straight back in; the
    routes treat an error as "no cool-down" deliberately, because a Redis
    outage must not lock every honest user out of booking — see the call sites.
    """
    ttl = await redis.ttl(cooldown_key(party, account_id))
    return ttl if ttl and ttl > 0 else 0


async def clear_cooldown(redis, party: str, account_id) -> None:
    """Remove the cool-down. Called when an operator overturns a default."""
    await redis.delete(cooldown_key(party, account_id))

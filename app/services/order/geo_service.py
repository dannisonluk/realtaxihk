"""Geo dispatch — Redis GEO index of BROADCASTING orders for nearby queries.

Index lifecycle mirrors order state:
- BROADCASTING  -> indexed (GEOADD)
- grabbed/cancelled -> removed (ZREM, idempotent)

SEC-25: the parallel `geo:drivers:online` index is gone. It was written on every
location tick and **never read by any code path**, and nothing ever expired it —
a live check found 37 stale members from drivers that had long since gone
offline. Online state already lives on `driver_profiles.is_online`, which is
queried from the database and cannot go stale independently.
"""

from __future__ import annotations

from app.core.config import get_settings

GEO_ORDERS_SUFFIX = "geo:orders:active"


def geo_orders_key() -> str:
    """The dispatch GEO index key, under the configured key namespace.

    `Settings.redis_key_namespace` is what keeps two concurrent pytest
    processes (or two CI shards) off the same key. Without the prefix they
    `GEOADD` and `GEOSEARCH` one shared index, so a test that asserts "this
    order is the nearest" intermittently sees another run's orders — the same
    collision the rate-limit keys already guard against (see
    `tests/conftest.py`). Read at call time rather than import time, so the
    namespace the app boots with is the one it uses.
    """
    return f"{get_settings().redis_key_namespace}{GEO_ORDERS_SUFFIX}"


class GeoService:
    def __init__(self, redis):
        self.redis = redis

    async def index_order(self, order_id: str, lat: float, lng: float) -> None:
        await self.redis.geoadd(geo_orders_key(), (lng, lat, str(order_id)))

    async def remove_order(self, order_id: str) -> None:
        await self.redis.zrem(geo_orders_key(), str(order_id))

    async def nearby_order_ids(
        self,
        lat: float,
        lng: float,
        radius_km: float,
        count: int = 50,
    ) -> list[str]:
        """Candidate order ids, nearest first, capped at `count`.

        The cap is the difference between "nearest 50" and "all of them", and it
        exists so a dense area cannot turn one map poll into an unbounded read.
        Callers that intend to filter the result in SQL must raise it: the Redis
        window is applied *before* any predicate, so a filtered query with the
        default cap silently answers a narrower question than it was asked. The
        caller owns that budget decision — see `nearby_orders` in
        `app/api/orders.py`.
        """
        res = await self.redis.geosearch(
            geo_orders_key(),
            longitude=lng,
            latitude=lat,
            radius=radius_km,
            unit="km",
            count=count,
            sort="ASC",
        )
        return [str(x) for x in res]

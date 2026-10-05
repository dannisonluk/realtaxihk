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

GEO_ORDERS_KEY = "geo:orders:active"


class GeoService:
    def __init__(self, redis):
        self.redis = redis

    async def index_order(self, order_id: str, lat: float, lng: float) -> None:
        await self.redis.geoadd(GEO_ORDERS_KEY, (lng, lat, str(order_id)))

    async def remove_order(self, order_id: str) -> None:
        await self.redis.zrem(GEO_ORDERS_KEY, str(order_id))

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
            GEO_ORDERS_KEY,
            longitude=lng,
            latitude=lat,
            radius=radius_km,
            unit="km",
            count=count,
            sort="ASC",
        )
        return [str(x) for x in res]

"""Geo dispatch — Redis GEO index of BROADCASTING orders for nearby queries.

Index lifecycle mirrors order state:
- BROADCASTING  -> indexed (GEOADD)
- grabbed/cancelled -> removed (ZREM, idempotent)
"""
from __future__ import annotations

GEO_ORDERS_KEY = "geo:orders:active"
GEO_DRIVERS_KEY = "geo:drivers:online"


class GeoService:
    def __init__(self, redis):
        self.redis = redis

    async def index_driver(self, driver_profile_id: str, lat: float, lng: float) -> None:
        await self.redis.geoadd(GEO_DRIVERS_KEY, (lng, lat, str(driver_profile_id)))

    async def remove_driver(self, driver_profile_id: str) -> None:
        await self.redis.zrem(GEO_DRIVERS_KEY, str(driver_profile_id))

    async def index_order(self, order_id: str, lat: float, lng: float) -> None:
        await self.redis.geoadd(GEO_ORDERS_KEY, (lng, lat, str(order_id)))

    async def remove_order(self, order_id: str) -> None:
        await self.redis.zrem(GEO_ORDERS_KEY, str(order_id))

    async def nearby_order_ids(self, lat: float, lng: float, radius_km: float) -> list[str]:
        res = await self.redis.geosearch(
            GEO_ORDERS_KEY,
            longitude=lng,
            latitude=lat,
            radius=radius_km,
            unit="km",
            count=50,
            sort="ASC",
        )
        return [str(x) for x in res]

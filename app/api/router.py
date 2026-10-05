"""Every API router, assembled in one place.

Why this module exists
----------------------
The routers used to be imported and included one by one inside
`app/main.py:create_app()`, which made the route table readable only by
reconstructing it in your head from twenty lines spread across a factory
function. The route table is the API's public surface, and it is now auditable
in one screen.

**Order is load-bearing and must not be tidied into alphabetical order.**
FastAPI matches in registration order, so a router mounted later can never claim
a path a router mounted earlier already matches. Three routers deliberately come
before the platform `admin` router because they live under the same
`/api/v1/admin` prefix and would otherwise be swallowed by it:

  * `admin_auth`     -- `/api/v1/admin/auth/*`
  * `admin_licence`  -- `/api/v1/admin/licence/*`
  * `admin_analytics` -- `/api/v1/admin/analytics/*`

The licence queue and the generic driver queue are different views of the same
domain and must not be conflated; the analytics routes share `/admin` and would
fall into the platform router's `/admin/orders/{order_id}`-style patterns.

`app/main.py` therefore includes this one router, and the sequence below is the
same sequence that used to live there. `app.openapi()` is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.admin_analytics import router as admin_analytics_router
from app.api.admin_auth import router as admin_auth_router
from app.api.admin_licence import router as admin_licence_router
from app.api.auth import router as auth_router
from app.api.destinations import router as destinations_router
from app.api.driver_attributes import router as driver_attributes_router
from app.api.drivers import router as drivers_router
from app.api.fare import router as fare_router
from app.api.fixed_offers import router as fixed_offers_router
from app.api.fleets import admin_router as admin_fleets_router
from app.api.fleets import router as fleets_router
from app.api.identity import router as identity_router
from app.api.licence import router as licence_router
from app.api.orders import router as orders_router
from app.api.recurring import router as recurring_router
from app.api.service_area_route import router as service_area_router
from app.api.tracking import router as tracking_router
from app.api.trips import router as trips_router
from app.api.ws import router as ws_router

api_router = APIRouter()

api_router.include_router(fare_router)
api_router.include_router(auth_router)
api_router.include_router(identity_router)
api_router.include_router(drivers_router)
api_router.include_router(driver_attributes_router)
api_router.include_router(fixed_offers_router)
api_router.include_router(destinations_router)

# Before the platform `admin_router`: `/api/v1/admin/auth/*` must reach the
# admin-auth handlers rather than falling into a catch-all.
api_router.include_router(admin_auth_router)

# Likewise before `admin_router`, so `/api/v1/admin/licence/*` reaches the
# licence-review handlers.
api_router.include_router(admin_licence_router)

# Likewise before `admin_router`, so `/api/v1/admin/analytics/*` is matched here.
api_router.include_router(admin_analytics_router)

api_router.include_router(admin_router)
api_router.include_router(licence_router)
api_router.include_router(fleets_router)
api_router.include_router(admin_fleets_router)
api_router.include_router(orders_router)
api_router.include_router(recurring_router)
api_router.include_router(tracking_router)
api_router.include_router(trips_router)
api_router.include_router(service_area_router)
api_router.include_router(ws_router)

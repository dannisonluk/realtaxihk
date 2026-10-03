"""Admin API — one module per resource.

This was a single 1,983-line module. The split follows the grouping the routes
already had: `/drivers`, `/settlement`, `/refunds`, `/audit`, `/accounts`,
`/orders`, `/disputes`, `/search`, `/live`. The sibling `app/api/admin_auth.py`,
`admin_licence.py` and `admin_analytics.py` were split out earlier; this finishes
the job.

The public surface is unchanged — `from app.api.admin import router` yields the
same router with the same prefix, tags and routes, registered in the same order,
so `app.openapi()` is identical to before the split.

Who may do what lives in `_roles.py`; the helpers more than one resource needs
live in `_shared.py`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import (
    accounts,
    audit,
    disputes,
    drivers,
    live,
    orders,
    refunds,
    search,
    settlement,
)

# Registration order matters: FastAPI matches routes in the order they were added,
# and the original module registered its sections in exactly this order.
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

for _resource in (
    drivers,
    settlement,
    refunds,
    audit,
    accounts,
    orders,
    disputes,
    search,
    live,
):
    router.include_router(_resource.router)

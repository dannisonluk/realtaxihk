"""Service-area schemas.

Kept in `app.api.schemas` rather than beside the route so the response-model
audit can find every operation's schema in the one namespace it walks.
"""

from pydantic import BaseModel

__all__ = ["ServiceAreaBounds", "ServiceAreaCheckResult"]


class ServiceAreaCheckResult(BaseModel):
    allowed: bool
    reason: str | None
    message: str | None = None
    lat: float
    lng: float


class ServiceAreaBounds(BaseModel):
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float

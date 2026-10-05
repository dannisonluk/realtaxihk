"""Fixed-fare offer schemas — driver-facing, not in the shared response package.

Requests stay in this module so `app.api.schemas` only exports response shapes.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

__all__ = ["FixedOfferIn", "FixedOfferListOut", "FixedOfferOut", "FixedOfferUpdateIn"]


class FixedOfferIn(BaseModel):
    destination_area: str | None = Field(default=None, max_length=24)
    premium_destination_id: str | None = None
    pickup_area: str | None = Field(default=None, max_length=24)
    price_hkd: Decimal = Field(gt=0, le=10000)


class FixedOfferUpdateIn(BaseModel):
    price_hkd: Decimal | None = Field(default=None, gt=0, le=10000)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|PAUSED)$")


class FixedOfferOut(BaseModel):
    id: str
    driver_profile_id: str
    destination_area: str | None
    premium_destination_id: str | None
    pickup_area: str | None
    price_hkd: str
    status: str
    created_at: str | None
    updated_at: str | None


class FixedOfferListOut(BaseModel):
    items: list[FixedOfferOut]

"""Premium destination schemas — the public pin list and admin CRUD views."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "PremiumDestinationIn",
    "PremiumDestinationListOut",
    "PremiumDestinationOut",
]


class PremiumDestinationIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name_zh: str = Field(min_length=1, max_length=120)
    name_en: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=200, ge=50, le=5000)
    avatar_key: str | None = Field(default=None, max_length=255)
    status: str = Field(default="ACTIVE", pattern=r"^(ACTIVE|HIDDEN)$")


class PremiumDestinationOut(BaseModel):
    id: str
    code: str
    name_zh: str
    name_en: str
    lat: float
    lng: float
    radius_m: int
    avatar_key: str | None = None
    status: str
    created_at: str


class PremiumDestinationListOut(BaseModel):
    items: list[PremiumDestinationOut]

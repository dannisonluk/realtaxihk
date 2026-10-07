"""Pre-booking API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import LandmarkCategory


class LandmarkOut(BaseModel):
    id: str
    code: str
    name_en: str
    name_zh: str
    category: str
    lat: float
    lng: float
    radius_m: int
    sort_order: int
    is_active: bool


class LandmarkListOut(BaseModel):
    items: list[LandmarkOut]


class DriverBookingPreferenceIn(BaseModel):
    categories: list[LandmarkCategory] = Field(default_factory=list)
    preferred_origin_area: str | None = None
    available_from: str | None = None
    available_until: str | None = None


class DriverBookingPreferenceOut(BaseModel):
    categories: list[str]
    preferred_origin_area: str | None
    available_from: str | None
    available_until: str | None
    updated_at: datetime


__all__ = [
    "DriverBookingPreferenceIn",
    "DriverBookingPreferenceOut",
    "LandmarkListOut",
    "LandmarkOut",
]

"""Wire models for the driver in-app notification inbox."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DriverNotificationOut(BaseModel):
    id: int
    order_id: str | None = None
    kind: str
    headline_zh: str
    headline_en: str
    body_zh: str
    body_en: str
    read: bool
    created_at: datetime


class DriverNotificationPageOut(BaseModel):
    items: list[DriverNotificationOut]
    next_cursor: int | None = None
    unread_count: int


class DriverNotificationReadOut(BaseModel):
    id: int
    read: bool = True


class DriverNotificationsReadAllOut(BaseModel):
    updated: int

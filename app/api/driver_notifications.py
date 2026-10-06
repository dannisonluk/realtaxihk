"""Driver in-app notification endpoints.

External push (WhatsApp / FCM / Google Maps) is out of scope by design. This
module serves the durable Postgres inbox and lets the mobile badge/list stay
fast: one list call returns both the page and the current unread count.

Authorisation is the same as the rest of the driver API: a user token plus a
live driver profile. A non-driver gets the same 404 as a missing profile so the
endpoint does not disclose which identities have driver rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.driver_notification import (
    DriverNotificationPageOut,
    DriverNotificationReadOut,
    DriverNotificationsReadAllOut,
)
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.models import DriverNotification, DriverProfile

router = APIRouter(
    prefix="/api/v1/drivers/me/notifications",
    tags=["drivers", "notifications"],
)


async def _require_driver(
    session: AsyncSession, user: Principal
) -> DriverProfile:
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="driver profile not found")
    return profile


def _out(row: DriverNotification) -> dict:
    return {
        "id": row.id,
        "order_id": str(row.order_id) if row.order_id else None,
        "kind": row.kind,
        "headline_zh": row.headline_zh,
        "headline_en": row.headline_en,
        "body_zh": row.body_zh,
        "body_en": row.body_en,
        "read": row.read_at is not None,
        "created_at": row.created_at,
    }


@router.get("", response_model=DriverNotificationPageOut)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    after_id: int | None = Query(default=None, ge=1),
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
) -> DriverNotificationPageOut:
    profile = await _require_driver(session, user)

    q = (
        select(DriverNotification)
        .where(DriverNotification.driver_profile_id == profile.id)
        .order_by(DriverNotification.id.desc())
        .limit(limit)
    )
    if after_id is not None:
        q = q.where(DriverNotification.id < after_id)

    rows = (await session.execute(q)).scalars().all()
    next_cursor = rows[-1].id if len(rows) == limit else None
    unread_count = await session.scalar(
        select(func.count())
        .select_from(DriverNotification)
        .where(
            DriverNotification.driver_profile_id == profile.id,
            DriverNotification.read_at.is_(None),
        )
    )

    return DriverNotificationPageOut(
        items=[_out(row) for row in rows],
        next_cursor=next_cursor,
        unread_count=unread_count or 0,
    )


@router.post("/read-all", response_model=DriverNotificationsReadAllOut)
async def read_all_notifications(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
) -> DriverNotificationsReadAllOut:
    profile = await _require_driver(session, user)
    result = await session.execute(
        update(DriverNotification)
        .where(
            DriverNotification.driver_profile_id == profile.id,
            DriverNotification.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    return DriverNotificationsReadAllOut(updated=result.rowcount or 0)


@router.post("/{notification_id}/read", response_model=DriverNotificationReadOut)
async def read_notification(
    notification_id: int,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
) -> DriverNotificationReadOut:
    profile = await _require_driver(session, user)
    notification = await session.get(DriverNotification, notification_id)
    if notification is None or notification.driver_profile_id != profile.id:
        raise HTTPException(status_code=404, detail="notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
    return DriverNotificationReadOut(id=notification.id, read=True)

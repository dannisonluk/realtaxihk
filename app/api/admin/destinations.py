"""Admin premium destinations CRUD.

The console adds, edits and hides the avatar-pinned places. OPERATIONS is the
right floor: these rows shape dispatch (what drivers see), so they are an
operations decision, not a money decision.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._roles import _require_operations
from app.api.schemas import PremiumDestinationListOut, PremiumDestinationOut
from app.api.schemas.premium import PremiumDestinationIn
from app.core.db import get_session
from app.core.deps import Principal
from app.models import PremiumDestination

router = APIRouter(prefix="/destinations", tags=["admin"])


class PremiumDestinationUpdateIn(BaseModel):
    name_zh: str | None = Field(default=None, max_length=120)
    name_en: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=50, le=5000)
    avatar_key: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|HIDDEN)$")


def _dest_out(d: PremiumDestination) -> dict:
    return {
        "id": str(d.id),
        "code": d.code,
        "name_zh": d.name_zh,
        "name_en": d.name_en,
        "lat": float(d.lat),
        "lng": float(d.lng),
        "radius_m": d.radius_m,
        "avatar_key": d.avatar_key,
        "status": str(d.status),
        "created_at": d.created_at.isoformat() if d.created_at else "",
    }


@router.get("", response_model=PremiumDestinationListOut)
async def list_premium_destinations(
    admin: Principal = Depends(_require_operations),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (await session.execute(select(PremiumDestination).order_by(PremiumDestination.name_zh)))
        .scalars()
        .all()
    )
    return {"items": [_dest_out(r) for r in rows]}


@router.post("", response_model=PremiumDestinationOut, status_code=201)
async def create_premium_destination(
    payload: PremiumDestinationIn,
    admin: Principal = Depends(_require_operations),
    session: AsyncSession = Depends(get_session),
):
    existing = (
        (
            await session.execute(
                select(PremiumDestination.id).where(PremiumDestination.code == payload.code)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="destination code already exists")
    dest = PremiumDestination(
        code=payload.code,
        name_zh=payload.name_zh,
        name_en=payload.name_en,
        lat=payload.lat,
        lng=payload.lng,
        radius_m=payload.radius_m,
        avatar_key=payload.avatar_key,
        status=payload.status,
        created_by=admin.id,
    )
    session.add(dest)
    await session.flush()
    return _dest_out(dest)


@router.patch("/{destination_id}", response_model=PremiumDestinationOut)
async def update_premium_destination(
    destination_id: uuid.UUID,
    payload: PremiumDestinationUpdateIn,
    admin: Principal = Depends(_require_operations),
    session: AsyncSession = Depends(get_session),
):
    dest = await session.get(PremiumDestination, destination_id)
    if dest is None:
        raise HTTPException(status_code=404, detail="destination not found")
    if payload.name_zh is not None:
        dest.name_zh = payload.name_zh
    if payload.name_en is not None:
        dest.name_en = payload.name_en
    if payload.lat is not None:
        dest.lat = payload.lat
    if payload.lng is not None:
        dest.lng = payload.lng
    if payload.radius_m is not None:
        dest.radius_m = payload.radius_m
    if payload.avatar_key is not None:
        dest.avatar_key = payload.avatar_key
    if payload.status is not None:
        dest.status = payload.status
    await session.flush()
    return _dest_out(dest)

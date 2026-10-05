"""Public premium destinations API — the map pins.

These are places, not people, and they are public metadata, so the read
endpoint is deliberately open. Writes stay in the admin console.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PremiumDestinationListOut
from app.core.db import get_session
from app.models import DestinationStatus, PremiumDestination

router = APIRouter(prefix="/api/v1/destinations", tags=["destinations"])


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
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(PremiumDestination)
                .where(PremiumDestination.status == DestinationStatus.ACTIVE)
                .order_by(PremiumDestination.name_zh)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_dest_out(r) for r in rows]}

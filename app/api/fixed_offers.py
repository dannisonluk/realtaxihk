"""Driver fixed-fare offer API (一口價)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FixedOfferListOut, FixedOfferOut
from app.api.schemas.fixed_offer import FixedOfferIn, FixedOfferUpdateIn
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.core.money import money_str
from app.models import DriverProfile, FixedOfferStatus, FixedPriceOffer
from app.services.fare.fixed_fare_service import FixedFareService

router = APIRouter(prefix="/api/v1/drivers/me/fixed-offers", tags=["drivers"])


async def _require_profile(session: AsyncSession, user: Principal) -> DriverProfile:
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no driver profile")
    return profile


def _offer_out(offer: FixedPriceOffer) -> dict:
    return {
        "id": str(offer.id),
        "driver_profile_id": str(offer.driver_profile_id),
        "destination_area": offer.destination_area,
        "premium_destination_id": (
            str(offer.premium_destination_id) if offer.premium_destination_id else None
        ),
        "pickup_area": offer.pickup_area,
        "price_hkd": money_str(offer.price_hkd),
        "status": offer.status,
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
        "updated_at": offer.updated_at.isoformat() if offer.updated_at else None,
    }


@router.get("", response_model=FixedOfferListOut)
async def list_fixed_offers(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    rows = (
        (
            await session.execute(
                select(FixedPriceOffer)
                .where(FixedPriceOffer.driver_profile_id == profile.id)
                .order_by(FixedPriceOffer.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_offer_out(r) for r in rows]}


@router.post("", status_code=201, response_model=FixedOfferOut)
async def create_fixed_offer(
    payload: FixedOfferIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    premium_id = (
        uuid.UUID(payload.premium_destination_id) if payload.premium_destination_id else None
    )
    try:
        offer = await FixedFareService(session).create_offer(
            driver_profile_id=profile.id,
            destination_area=payload.destination_area,
            premium_destination_id=premium_id,
            pickup_area=payload.pickup_area,
            price_hkd=payload.price_hkd,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await session.commit()
    await session.refresh(offer)
    return _offer_out(offer)


@router.patch("/{offer_id}", response_model=FixedOfferOut)
async def update_fixed_offer(
    offer_id: uuid.UUID,
    payload: FixedOfferUpdateIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    profile = await _require_profile(session, user)
    offer = await session.get(FixedPriceOffer, offer_id)
    if offer is None or offer.driver_profile_id != profile.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
    if payload.price_hkd is not None:
        offer.price_hkd = payload.price_hkd
    if payload.status is not None:
        offer.status = FixedOfferStatus(payload.status).value
    await session.commit()
    await session.refresh(offer)
    return _offer_out(offer)

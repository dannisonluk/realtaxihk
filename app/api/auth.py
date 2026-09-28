"""Auth API: WhatsApp OTP request/verify -> JWT. PDPO: phones masked in output."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, get_current_user
from app.core.exceptions import BusinessRuleError
from app.core.masking import mask_phone
from app.core.security import create_access_token
from app.models import UserRole
from app.services.otp_service import OtpService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class OtpRequestIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+852\d{8}$")


class OtpVerifyIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+852\d{8}$")
    code: str = Field(pattern=r"^\d{6}$")


def _user_out(user) -> dict:
    return {
        "id": str(user.id),
        "phone_masked": mask_phone(user.phone_e164),
        "role": user.role.value,
    }


@router.post("/otp/request")
async def otp_request(payload: OtpRequestIn, session: AsyncSession = Depends(get_session)):
    try:
        result = await OtpService(session).request_otp(payload.phone_e164)
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    return result


@router.post("/otp/verify")
async def otp_verify(payload: OtpVerifyIn, session: AsyncSession = Depends(get_session)):
    try:
        auth = await OtpService(session).verify_otp(payload.phone_e164, payload.code)
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    token = create_access_token({"sub": str(auth.user.id), "role": auth.user.role.value})
    return {
        "access_token": token,
        "token_type": "bearer",
        "created": auth.created,
        "user": _user_out(auth.user),
    }


@router.get("/me")
async def me(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models import User

    db_user = await session.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "id": str(db_user.id),
        "phone_masked": mask_phone(db_user.phone_e164),
        "role": db_user.role.value,
    }


def require_role(role: UserRole):
    async def _guard(user: Principal = Depends(get_current_user)) -> Principal:
        if user.role != role:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"{role.value} role required"
            )
        return user

    return _guard

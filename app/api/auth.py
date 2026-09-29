"""Auth API: WhatsApp OTP request/verify -> JWT + rotating refresh tokens.

PDPO: phones masked in output. P1-2: OTP requests are rate-limited per IP and
globally (cost cap). P1-5: login returns access + refresh; /auth/refresh
rotates; /auth/logout revokes everything for the caller.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.core.exceptions import BusinessRuleError
from app.core.masking import mask_phone
from app.core.security import create_access_token
from app.models import User, UserRole
from app.services.otp_service import OtpService
from app.services.refresh_service import RefreshService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class OtpRequestIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+852\d{8}$")


class OtpVerifyIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+852\d{8}$")
    code: str = Field(pattern=r"^\d{6}$")


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)


def _user_out(user) -> dict:
    return {
        "id": str(user.id),
        "phone_masked": mask_phone(user.phone_e164),
        "role": user.role.value,
    }


def _client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For when behind the reverse proxy (nginx sets it)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/otp/request")
async def otp_request(
    payload: OtpRequestIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    limiter = request.app.state.rate_limiter
    ip = _client_ip(request)
    if not await limiter.allow(
        f"otp:ip:{ip}", settings.otp_ip_rate_limit, settings.otp_ip_window_s
    ):
        raise HTTPException(status_code=429, detail="too many OTP requests from this address")
    if not await limiter.allow(
        "otp:global:hourly", settings.otp_global_hourly_limit, 3600
    ):
        raise HTTPException(status_code=429, detail="OTP volume cap reached, try again later")
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
    refresh = await RefreshService(session).issue(auth.user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "refresh_token": refresh,
        "created": auth.created,
        "user": _user_out(auth.user),
    }


@router.post("/refresh")
async def refresh_tokens(payload: RefreshIn, session: AsyncSession = Depends(get_session)):
    result = await RefreshService(session).rotate(payload.refresh_token)
    if result is None:
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    user_id, new_refresh = result
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {
        "access_token": token,
        "token_type": "bearer",
        "refresh_token": new_refresh,
        "user": _user_out(user),
    }


@router.post("/logout")
async def logout(user: Principal = Depends(require_active_user),
                 session: AsyncSession = Depends(get_session)):
    revoked = await RefreshService(session).revoke_all_for_user(user.id)
    return {"ok": True, "revoked": revoked}


@router.get("/me")
async def me(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await session.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "id": str(db_user.id),
        "phone_masked": mask_phone(db_user.phone_e164),
        "role": db_user.role.value,
    }


def require_role(role: UserRole):
    async def _guard(user: Principal = Depends(require_active_user)) -> Principal:
        if user.role != role:
            raise HTTPException(
                status_code=403, detail=f"{role.value} role required"
            )
        return user

    return _guard

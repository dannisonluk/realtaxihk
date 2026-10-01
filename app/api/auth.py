"""Auth API: WhatsApp OTP request/verify -> JWT + rotating refresh tokens.

PDPO: phones masked in output. P1-2: OTP requests are rate-limited per IP and
globally (cost cap). P1-5: login returns access + refresh; /auth/refresh
rotates; /auth/logout revokes everything for the caller.

Security (SEC-07/08/17/18):
- the client address is derived from the RIGHT of X-Forwarded-For, and only when
  a trusted proxy is configured — a client-supplied prefix cannot forge its IP;
- the platform-wide OTP ceiling is a *signal* that tightens the per-phone budget
  and alerts, never a shared counter that one attacker can use to lock everyone
  out of the platform;
- a replayed refresh token revokes the whole token family AND the user's access
  tokens (via the revocation epoch);
- logout revokes access tokens too, not just refresh tokens.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AdminMeOut,
    AuthMeOut,
    OkRevokedOut,
    OtpRequestOut,
    TokenPairOut,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require_active_user, require_live_principal
from app.core.exceptions import BusinessRuleError
from app.core.masking import mask_email, mask_phone
from app.core.security import create_access_token
from app.core.token_revocation import revoke_user_tokens
from app.models import AdminAccount, User, UserRole
from app.services import phone_reverify_service as phone_reverify
from app.services.otp_service import OtpService
from app.services.refresh_service import RefreshService

logger = logging.getLogger("realtaxihk.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_VERIFY_IP_RATE_LIMIT = 60
_VERIFY_IP_WINDOW_S = 60


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
    """Resolve the caller's address without trusting client-supplied headers.

    SEC-07: the previous implementation took `x_forwarded_for.split(",")[0]` —
    the LEFTMOST element, which is exactly the part an attacker controls. nginx's
    `$proxy_add_x_forwarded_for` *appends* the real peer, so even a correct
    deployment left the attacker-controlled prefix in position 0 and every IP
    rate limit was bypassable by rotating the header.

    Now: X-Forwarded-For is ignored entirely unless `TRUSTED_PROXY_COUNT > 0`,
    and when it is read we count hops from the RIGHT — `[-trusted]` is the peer
    as seen by the outermost trusted proxy, which no client can forge.
    """
    settings = get_settings()
    if settings.trusted_proxy_count > 0:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                idx = max(0, len(hops) - settings.trusted_proxy_count)
                return hops[idx]
    return request.client.host if request.client else "unknown"


async def _revoke_access_tokens(request: Request, user_id) -> None:
    """Set the user's revocation epoch so every issued access token dies.

    The client is the app's shared per-loop one and is closed at shutdown —
    closing it here would disconnect every other request sharing it.
    """
    await revoke_user_tokens(request.app.state.redis_factory(), user_id)


@router.post("/otp/request", response_model=OtpRequestOut)
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

    # SEC-08: platform-wide volume is monitored and used to DEGRADE, not to gate.
    # The old code 429'd everyone once a single shared counter hit the cap, so one
    # attacker with 500 requests could lock the entire user base out of login.
    global_count = await limiter.count("otp:global:hourly", 3600)
    if global_count > settings.otp_global_hourly_hard_limit:
        logger.critical(
            "OTP volume %d exceeded HARD ceiling %d — shedding load",
            global_count,
            settings.otp_global_hourly_hard_limit,
        )
        raise HTTPException(
            status_code=503,
            detail="OTP service is temporarily unavailable, please retry later",
            headers={"Retry-After": "600"},
        )

    phone_limit = settings.otp_phone_rate_limit
    if global_count > settings.otp_global_hourly_limit:
        # Alert + tighten the per-number budget instead of denying everyone.
        logger.critical(
            "OTP volume %d over soft cap %d — degrading per-phone limit to %d",
            global_count,
            settings.otp_global_hourly_limit,
            settings.otp_phone_rate_limit_strict,
        )
        phone_limit = settings.otp_phone_rate_limit_strict

    if not await limiter.allow(
        f"otp:phone:{payload.phone_e164}", phone_limit, settings.otp_phone_window_s
    ):
        raise HTTPException(status_code=429, detail="too many OTP requests for this number")

    try:
        result = await OtpService(session).request_otp(payload.phone_e164)
    except BusinessRuleError:
        # `BusinessRuleError` subclasses `ValueError`, so without this branch the
        # generic handler below would rebuild it from `str(exc)` and silently
        # drop `details`. That is where the resend cooldown's
        # `retry_after_seconds` and the invalid-code `attempts_remaining` live —
        # the client cannot render a countdown without them.
        raise
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    return result


@router.post("/otp/verify", response_model=TokenPairOut)
async def otp_verify(
    payload: OtpVerifyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"otp:verify:ip:{_client_ip(request)}", _VERIFY_IP_RATE_LIMIT, _VERIFY_IP_WINDOW_S
    ):
        raise HTTPException(status_code=429, detail="too many verification attempts")

    try:
        auth = await OtpService(session).verify_otp(payload.phone_e164, payload.code)
    except BusinessRuleError:
        # `BusinessRuleError` subclasses `ValueError`, so without this branch the
        # generic handler below would rebuild it from `str(exc)` and silently
        # drop `details`. That is where the resend cooldown's
        # `retry_after_seconds` and the invalid-code `attempts_remaining` live —
        # the client cannot render a countdown without them.
        raise
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc

    # P-4: a login OTP proves the number just as a dedicated re-verify does, so it
    # must reset the monthly clock too. Omitting this would produce the worst
    # version of the bug: users who log in every day are the most obviously
    # reachable, yet their deadline only moves if they happen to walk into the
    # re-verify screen. Newly created accounts already got a deadline in
    # `verify_otp`; this covers the existing ones, whose window is either expired
    # or approaching.
    if not auth.created:
        await phone_reverify.mark_verified(session, auth.user)
        await session.commit()

    token = create_access_token({"sub": str(auth.user.id), "role": auth.user.role.value})
    refresh = await RefreshService(session).issue(auth.user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "refresh_token": refresh,
        "created": auth.created,
        "user": _user_out(auth.user),
    }


@router.post("/refresh", response_model=TokenPairOut)
async def refresh_tokens(
    payload: RefreshIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    outcome = await RefreshService(session).rotate(payload.refresh_token)

    if outcome.reused:
        # SEC-17: a rotated token came back. Assume it was stolen — kill the whole
        # family (refresh tokens) and every access token the user holds.
        # Commit explicitly: raising below unwinds the request-scoped session, and
        # `get_session` rolls back on exception — without this the family
        # revocation would be discarded and the attacker's token would survive.
        await session.commit()
        logger.critical(
            "refresh token replay detected for user %s — revoking all sessions", outcome.user_id
        )
        await _revoke_access_tokens(request, outcome.user_id)
        raise HTTPException(
            status_code=401, detail="refresh token reuse detected — all sessions revoked"
        )

    if outcome.new_refresh is None or outcome.user_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")

    user = await session.get(User, outcome.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {
        "access_token": token,
        "token_type": "bearer",
        "refresh_token": outcome.new_refresh,
        "user": _user_out(user),
    }


@router.post("/logout", response_model=OkRevokedOut)
async def logout(
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    revoked = await RefreshService(session).revoke_all_for_user(user.id)
    # SEC-18: also invalidate the access token the caller is holding (and any
    # other one already issued to this user), not just the refresh tokens.
    await _revoke_access_tokens(request, user.id)
    return {"ok": True, "revoked": revoked}


@router.get("/me", response_model=AuthMeOut | AdminMeOut)
async def me(
    user: Principal = Depends(require_live_principal),
    session: AsyncSession = Depends(get_session),
):
    """Who is this token?

    Gated by `require_live_principal` rather than `require_active_user`. The
    console shell calls this on boot to decide whether the session it has in
    storage is still good, and it carries an **admin** token — so resolving the
    principal against `users` (which `require_active_user` does) reported
    "account not found" for a perfectly valid admin session. The guard reads the
    row from whichever table the token's `scope` names, so the liveness check is
    not skipped, just performed against the right table.
    """
    if user.is_admin:
        # Re-read rather than trusting `user` for the display fields. Not an
        # `assert` that the row exists: `assert` is stripped under `-O`, and
        # this is a lookup that can genuinely miss if the row is deleted between
        # the dependency and the handler.
        admin = await session.get(AdminAccount, user.id)
        if admin is None:
            raise HTTPException(status_code=404, detail="admin not found")
        return {
            "id": str(admin.id),
            "username": admin.username,
            "email_masked": mask_email(admin.email),
            "role": UserRole.ADMIN.value,
            "admin_role": admin.admin_role.value,
        }

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
            raise HTTPException(status_code=403, detail=f"{role.value} role required")
        return user

    return _guard

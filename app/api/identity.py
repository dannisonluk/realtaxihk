"""Registration identity API (P-2): profile completion and email verification.

Complements `app.api.auth`, which owns the phone-OTP half. An account is created
there on the first successful OTP verify; this router finishes it.

    POST /api/v1/identity/profile          — username, names, gender, avatar key
    GET  /api/v1/identity/username-check   — availability, for live form feedback
    POST /api/v1/identity/email/request    — send a verification link
    POST /api/v1/identity/email/confirm    — consume the link
    POST /api/v1/identity/phone/reverify   — P-4: re-prove the phone, reset the clock
    GET  /api/v1/identity/me               — the full profile

The confirm endpoint is deliberately **unauthenticated**. The link is usually
opened in a different browser or on a different device from the app session, and
requiring a bearer token would make it unusable exactly when it is needed. The
token in the link is the credential — 256 bits, single-use, expiring — which is
why it can stand alone.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AvatarPresignOut,
    EmailConfirmOut,
    EmailRequestOut,
    PhoneReverifyOut,
    ProfileOut,
    UsernameCheckOut,
)
from app.core.client_ip import client_ip
from app.core.db import get_session
from app.core.deps import Principal, require_active_user
from app.core.exceptions import BusinessRuleError
from app.core.masking import mask_phone
from app.models import Gender, User
from app.services.auth import phone_reverify_service as phone_reverify
from app.services.auth.identity_service import IdentityService
from app.services.licence.storage_service import get_storage_service

logger = logging.getLogger("realtaxihk.identity")

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])

_EMAIL_IP_RATE_LIMIT = 10  # verification emails per IP per window
_EMAIL_IP_WINDOW_S = 3600
_CHECK_IP_RATE_LIMIT = 120  # username availability, polled as the user types
_CHECK_IP_WINDOW_S = 60
_PHONE_REVERIFY_IP_RATE_LIMIT = 20  # lower than login's 60: this is not a launch path
_PHONE_REVERIFY_IP_WINDOW_S = 3600


class ProfileIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    given_name: str = Field(min_length=1, max_length=60)
    family_name: str = Field(min_length=1, max_length=60)
    gender: Gender | None = None
    # An R2 object key, never a URL — the service rejects anything with a scheme
    # so a `javascript:` value cannot reach an `<img src>`.
    avatar_key: str | None = Field(default=None, max_length=255)


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ConfirmIn(BaseModel):
    token: str = Field(min_length=16, max_length=512)


def _profile_out(user: User) -> dict:
    state = phone_reverify.evaluate(user)
    return {
        "id": str(user.id),
        "username": user.username,
        "given_name": user.given_name,
        "family_name": user.family_name,
        "gender": user.gender.value if user.gender else None,
        "avatar_key": user.avatar_key,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "phone_masked": mask_phone(user.phone_e164),
        "phone_verified": user.phone_verified_at is not None,
        "phone_reverify_due_at": (
            user.phone_reverify_due_at.isoformat() if user.phone_reverify_due_at else None
        ),
        # P-4: the derived state, not just the raw deadline. `is_due` and
        # `is_blocked` are separate on purpose — a reminder and a soft block are
        # different moments a week apart, and the client needs to tell them
        # apart to decide between a banner and a modal.
        **state.as_dict(),
        "account_status": user.account_status.value,
        "role": user.role.value,
    }


async def _run(coro, session: AsyncSession):
    """Map a service refusal onto HTTP, **committing the audit-relevant writes**.

    Commits before raising for the same reason as `app.api.admin_auth._run`:
    `get_session` rolls back on exception, and a refusal is an exception. Without
    this, a `consumed_at` stamp written just before a later check fails would be
    silently discarded — the token would stay usable.
    """
    try:
        return await coro
    except BusinessRuleError as exc:
        await session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me", response_model=ProfileOut)
async def me(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _profile_out(row)


@router.post("/profile", response_model=ProfileOut)
async def complete_profile(
    payload: ProfileIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    _result = await _run(
        IdentityService(session).complete_profile(
            row,
            username=payload.username,
            given_name=payload.given_name,
            family_name=payload.family_name,
            gender=payload.gender,
            avatar_key=payload.avatar_key,
        ),
        session,
    )
    await session.commit()
    # The full profile, not just the echoed username: completing it may be what
    # flipped the account to ACTIVE, and the client needs to see that.
    return _profile_out(row)


@router.get("/username-check", response_model=UsernameCheckOut)
async def username_check(
    username: str,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Availability for live form feedback.

    Rate-limited because it is an unauthenticated-in-spirit enumeration surface
    otherwise: polled on every keystroke, it would let anyone walk the username
    space. The limiter caps that without making the field feel broken.
    """
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"identity:username-check:{client_ip(request)}",
        _CHECK_IP_RATE_LIMIT,
        _CHECK_IP_WINDOW_S,
    ):
        raise HTTPException(status_code=429, detail="too many checks — slow down")

    available = await IdentityService(session).username_available(username)
    return {"username": username.strip().lower(), "available": available}


@router.post("/email/request", response_model=EmailRequestOut)
async def request_email(
    payload: EmailIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"identity:email:{client_ip(request)}", _EMAIL_IP_RATE_LIMIT, _EMAIL_IP_WINDOW_S
    ):
        raise HTTPException(status_code=429, detail="too many verification emails — try later")

    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    result = await _run(
        IdentityService(session).request_email_verification(row, payload.email), session
    )
    await session.commit()
    return result


@router.post("/email/confirm", response_model=EmailConfirmOut)
async def confirm_email(
    payload: ConfirmIn,
    session: AsyncSession = Depends(get_session),
):
    """Consume a verification link. No bearer token — see the module docstring."""
    row = await _run(IdentityService(session).confirm_email(payload.token), session)
    await session.commit()
    return {
        "verified": True,
        "email": row.email,
        "account_status": row.account_status.value,
    }


class PhoneReverifyIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+852\d{8}$")
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/phone/reverify", response_model=PhoneReverifyOut)
async def reverify_phone(
    payload: PhoneReverifyIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P-4: re-prove the phone number and push the next deadline out a month.

    Deliberately guarded by `require_active_user` and **not** by
    `require_phone_current`. The whole point of a soft block is that an overdue
    account can still fix itself: gating the remedy behind the condition would
    make an overdue account permanently unable to clear it, which is the one way
    a soft block becomes a lockout.

    The OTP itself is verified by `OtpService`, the same code path as login — the
    number has to be proven by a code sent to it, not merely asserted. What is
    new here is the second half: `mark_verified` moves `phone_reverify_due_at`
    forward. Without that the caller would verify and stay blocked.
    """
    from app.services.auth.otp_service import OtpService

    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"identity:phone-reverify:{client_ip(request)}",
        _PHONE_REVERIFY_IP_RATE_LIMIT,
        _PHONE_REVERIFY_IP_WINDOW_S,
    ):
        raise HTTPException(status_code=429, detail="too many verification attempts")

    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    if payload.phone_e164 != row.phone_e164:
        # A re-verify proves a number you already own. Accepting a new one here
        # would make this endpoint a silent phone-change primitive, bypassing
        # whatever safeguards a real change-of-number flow needs.
        raise HTTPException(status_code=400, detail="this number is not the one on your account")

    created = await _run(OtpService(session).verify_otp_for_user(row, payload.code), session)
    # `verify_otp_for_user` refuses unless the code was issued for *this* row's
    # number, so the deadline can be moved with confidence: the code proved the
    # phone on this account, not merely that someone somewhere held a valid OTP.
    await phone_reverify.mark_verified(session, row)
    await session.commit()
    logger.info("phone re-verified user=%s created=%s", row.id, created)
    return {"verified": True, "created": created, **_profile_out(row)}


class AvatarUploadIn(BaseModel):
    """Request a presigned PUT for an avatar.

    `size_bytes` is required, not optional: the ceiling is checked at *signing*
    time. Signing first and rejecting the upload afterwards means the bytes have
    already reached the bucket and will sit there — which is a storage bill and,
    worse, an object nobody has a row for.
    """

    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)


@router.post("/avatar/uploads", response_model=AvatarPresignOut)
async def presign_avatar_upload(
    payload: AvatarUploadIn,
    user: Principal = Depends(require_active_user),
):
    """Mint an avatar key and a URL to upload to.

    Guarded by `require_active_user` rather than the weaker `get_current_user`:
    an UNVERIFIED account uploading bytes is storage we have no reason to hold
    and no way to attribute, and registration is where a script would land.

    **Writes nothing.** No `avatar_key` is set here — a row updated at presign
    time would point at an object that may never arrive. The key is claimed by
    the client on `POST /identity/profile`, where the value can be validated,
    and until then the old avatar (or none) stays correct.
    """
    storage = get_storage_service()
    presigned = storage.presign_avatar(
        user_id=str(user.id),
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
    )
    return {
        "upload_url": presigned.upload_url,
        "object_key": presigned.object_key,
        "expires_in": presigned.expires_in,
        "headers": presigned.headers,
    }

"""Admin authentication API: password + email identity, and a TOTP second factor.

This is deliberately a **separate router** from `app.api.auth`. Admin accounts
live in their own table (`admin_accounts`), authenticated by username/password
rather than the phone-OTP flow the passenger/driver app uses, and they carry a
mandatory second factor. Sharing a router would have meant a `role` branch on
every handler, which is exactly how a passenger request ends up in an
admin-authenticated code path.

The three-step login state machine
---------------------------------
1. `POST /login`      — username + password. Never returns an access token.
2. `POST /totp/verify` — 6-digit code, or `POST /recovery` with a recovery code.
3. (both of the above return the access token; `/login` returns only a
   short-lived *challenge* token)

Why the challenge token matters: `principal_from_token` requires both `sub` and
`role`, and the challenge carries neither. So even if it leaked into an
`Authorization` header it could not be used as an access token — the failure is
structural, not a check somebody could later "optimise" away.

Fixing the enrolment trap
-------------------------
`/totp/enrol` returns a secret and a QR URI **but does not write it to the
account**. The pending secret waits in Redis until a code generated from it is
proven, at which point `/totp/enrol/confirm` persists it. The naive version
(store the secret, then show the QR) locks an admin out permanently the moment
they close the tab before scanning — the account now demands a second factor
nobody holds.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import BusinessRuleError
from app.services.admin_auth_service import (
    AdminAuthError,
    AdminAuthService,
    issue_admin_access_token,
)

logger = logging.getLogger("realtaxihk.admin_auth")

router = APIRouter(prefix="/api/v1/admin/auth", tags=["admin-auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TotpIn(BaseModel):
    challenge_token: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=6, max_length=7)


class RecoveryIn(BaseModel):
    challenge_token: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=8, max_length=64)


class ConfirmEnrolIn(BaseModel):
    challenge_token: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=6, max_length=7)


def _client_ip(request: Request) -> str:
    """Resolve the caller address, trusting X-Forwarded-For only behind a proxy.

    Same rule as `app.api.auth._client_ip` (SEC-07): read hops from the RIGHT.
    The leftmost element is the part a client controls, so counting from there
    would make every admin rate limit bypassable by rotating a header.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.trusted_proxy_count > 0:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                idx = max(0, len(hops) - settings.trusted_proxy_count)
                return hops[idx]
    return request.client.host if request.client else "unknown"


def _service(request: Request, session: AsyncSession) -> AdminAuthService:
    # The app-lifetime client, not `redis_factory()` — the latter hands out one
    # client per request, and the login path already touches Redis twice.
    return AdminAuthService(session, request.app.state.auth_redis)


async def _run(coro, session: AsyncSession):
    """Run a service call, mapping its errors onto HTTP statuses.

    **Commits before raising.** This is the non-obvious part. `get_session`
    rolls back on exception, and every refusal here is an exception — so without
    this commit, `_register_failure`'s increment, the lockout it triggers, and
    the `ADMIN_LOGIN_FAILED` audit row would all be rolled back and silently
    discarded. The visible symptom was an account that accepted unlimited wrong
    passwords: the counter never survived the request. It also meant failed
    logins left no trace, which defeats the point of an audit log.

    `AdminAuthError` is a `BusinessRuleError` subclass, so it is caught first —
    otherwise the generic branch would rebuild it from `str(exc)` and lose the
    distinction between "bad credentials" (401) and "throttled" (429).
    """
    try:
        return await coro
    except AdminAuthError as exc:
        await session.commit()
        status = 429 if "too many" in str(exc) else 401
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        await session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
async def login(
    payload: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Step 1: username + password. Returns a challenge, never an access token."""
    svc = _service(request, session)
    outcome = await _run(
        svc.login(username=payload.username, password=payload.password, ip=_client_ip(request)),
        session,
    )

    body: dict = {"next": outcome.kind}
    if outcome.challenge_token:
        # Both `totp_required` and `enrolment_required` carry one; the purposes
        # differ, so `verify_totp` cannot consume an enrolment challenge.
        body["challenge_token"] = outcome.challenge_token
    if outcome.enrolment is not None:
        # First login: hand over everything needed to set up the authenticator.
        # Returned once — the secret is never re-readable from the account.
        body["enrolment"] = {
            "secret": outcome.enrolment.secret,
            "otpauth_uri": outcome.enrolment.otpauth_uri,
            "recovery_codes": outcome.enrolment.recovery_codes,
        }
    return body


@router.post("/totp/verify")
async def verify_totp(
    payload: TotpIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Step 2a: prove the second factor with a 6-digit authenticator code.

    Only this (or `/totp/enrol/confirm`, or a recovery code) returns an access
    token. A correct password on its own never does.
    """
    svc = _service(request, session)
    account = await _run(
        svc.verify_totp(
            challenge_token=payload.challenge_token, code=payload.code, ip=_client_ip(request)
        ),
        session,
    )
    return {
        "access_token": issue_admin_access_token(account),
        "token_type": "bearer",
        "admin": _admin_out(account),
    }


@router.post("/recovery")
async def recovery(
    payload: RecoveryIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Step 2b: prove the second factor with a single-use recovery code.

    For the lost-phone case. The step-1 challenge token names the account, so
    the username does not have to be resubmitted — and the code is still
    verified against that account only, never searched globally.
    """
    svc = _service(request, session)
    account = await _run(
        svc.consume_recovery_code(
            challenge_token=payload.challenge_token, code=payload.code, ip=_client_ip(request)
        ),
        session,
    )
    return {
        "access_token": issue_admin_access_token(account),
        "token_type": "bearer",
        "admin": _admin_out(account),
    }


@router.post("/totp/enrol")
async def begin_enrolment(
    payload: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Re-issue enrolment material for an admin who never finished setup.

    Requires the password again. `/login` already returns the material on the
    first login; this exists for the case where that response was lost (tab
    closed, network dropped) and the Redis-held secret has expired.
    """
    svc = _service(request, session)
    outcome = await _run(
        svc.login(username=payload.username, password=payload.password, ip=_client_ip(request)),
        session,
    )
    if outcome.enrolment is None:
        raise HTTPException(
            status_code=409, detail="this account has already enrolled a TOTP device"
        )
    return {
        "next": outcome.kind,
        "challenge_token": outcome.challenge_token,
        "enrolment": {
            "secret": outcome.enrolment.secret,
            "otpauth_uri": outcome.enrolment.otpauth_uri,
            "recovery_codes": outcome.enrolment.recovery_codes,
        },
    }


@router.post("/totp/enrol/confirm")
async def confirm_enrolment(
    payload: ConfirmEnrolIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Finish enrolment: prove one code from the pending secret, then persist it.

    The challenge token goes in the body, not the Authorization header — it is
    not an access token and must not be presented as one.
    """
    svc = _service(request, session)
    account = await _run(
        svc.complete_enrolment(
            challenge_token=payload.challenge_token, code=payload.code, ip=_client_ip(request)
        ),
        session,
    )
    return {
        "access_token": issue_admin_access_token(account),
        "token_type": "bearer",
        "admin": _admin_out(account),
    }


def _admin_out(account) -> dict:
    return {
        "id": str(account.id),
        "username": account.username,
        "email": account.email,
        "full_name": account.full_name,
        "totp_enrolled": account.totp_enrolled_at is not None,
    }

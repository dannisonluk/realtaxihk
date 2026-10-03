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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AdminLoginOut,
    AdminSessionOut,
    OkLogoutOut,
    TotpEnrolOut,
)
from app.core.admin_cookies import (
    clear_session_cookie_headers,
    clear_session_cookies,
    read_csrf_header,
    read_refresh_cookie,
    set_session_cookies,
)
from app.core.client_ip import client_ip
from app.core.db import get_session
from app.core.deps import require_live_admin_refresh_session
from app.core.exceptions import BusinessRuleError
from app.core.token_revocation import revoke_user_tokens
from app.models import AdminAccount
from app.services.admin.admin_auth_service import (
    AdminAccountLocked,
    AdminAuthError,
    AdminAuthService,
    AdminAuthThrottled,
    issue_admin_access_token,
)
from app.services.admin.admin_refresh_service import AdminRefreshService

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

    The 429/401 split is decided by **exception type**, not by matching the
    message text. It used to be `429 if "too many" in str(exc) else 401`, which
    made a security-relevant status code depend on English wording: rewording a
    message, or adding a new one containing the substring, would silently move
    an attempt between "rejected" and "throttled". `AdminAuthThrottled` makes
    the mapping structural.

    **A lockout answers 401, not 429 — deliberately.** A rate limit says "the
    endpoint refuses you"; a lockout says "this account refuses you". Reporting
    those differently would let an attacker distinguish a locked *account* from
    an ordinary rate-limited *IP*, i.e. confirm that a username exists and has
    been guessed at enough to trip the lock. `test_lockout_does_not_expose_is_active`
    pins this. `AdminAccountLocked` therefore subclasses `AdminAuthThrottled`
    but is mapped to 401, and the distinction survives in the log, not the wire.
    """
    try:
        return await coro
    except AdminAccountLocked as exc:
        # Not 429: see the docstring. The account-level lock must not be
        # distinguishable from a credential rejection at the API edge.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except AdminAuthThrottled as exc:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except AdminAuthError as exc:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        await session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=AdminLoginOut)
async def login(
    payload: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Step 1: username + password. Returns a challenge, never an access token."""
    svc = _service(request, session)
    outcome = await _run(
        svc.login(username=payload.username, password=payload.password, ip=client_ip(request)),
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


@router.post("/totp/verify", response_model=AdminSessionOut)
async def verify_totp(
    payload: TotpIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Step 2a: prove the second factor with a 6-digit authenticator code.

    Only this (or `/totp/enrol/confirm`, or a recovery code) returns an access
    token. A correct password on its own never does.
    """
    svc = _service(request, session)
    account = await _run(
        svc.verify_totp(
            challenge_token=payload.challenge_token, code=payload.code, ip=client_ip(request)
        ),
        session,
    )
    return await _issue_session(response, session, account)


@router.post("/recovery", response_model=AdminSessionOut)
async def recovery(
    payload: RecoveryIn,
    request: Request,
    response: Response,
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
            challenge_token=payload.challenge_token, code=payload.code, ip=client_ip(request)
        ),
        session,
    )
    return await _issue_session(response, session, account)


@router.post("/totp/enrol", response_model=TotpEnrolOut)
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
        svc.login(username=payload.username, password=payload.password, ip=client_ip(request)),
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


@router.post("/totp/enrol/confirm", response_model=AdminSessionOut)
async def confirm_enrolment(
    payload: ConfirmEnrolIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Finish enrolment: prove one code from the pending secret, then persist it.

    The challenge token goes in the body, not the Authorization header — it is
    not an access token and must not be presented as one.
    """
    svc = _service(request, session)
    account = await _run(
        svc.complete_enrolment(
            challenge_token=payload.challenge_token, code=payload.code, ip=client_ip(request)
        ),
        session,
    )
    return await _issue_session(response, session, account)


@router.post("/refresh", response_model=AdminSessionOut)
async def refresh_admin_session(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _live: None = Depends(require_live_admin_refresh_session),
):
    """Exchange the refresh cookie for a new access token.

    Takes **no body**. Everything it needs is already in the cookies: the
    refresh token (HttpOnly, so script never saw it) and the CSRF token (which
    the console read and echoed in `X-CSRF-Token`). That signature is the point
    — an endpoint that accepted a token from the request body would reintroduce
    exactly the `sessionStorage` exposure the cookie exists to remove.

    A refused refresh is a plain 401 in every case, deliberately. The client's
    only correct response is to show the login screen, so there is no useful
    distinction to draw between "no cookie", "expired", "replayed" and "wrong
    CSRF" on the wire — and drawing one would tell an attacker which of those
    they had achieved. The distinction lives in the log and the audit trail.
    """
    raw_refresh = read_refresh_cookie(request)
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="no refresh cookie")

    outcome = await AdminRefreshService(session).rotate(raw_refresh, read_csrf_header(request))

    if outcome.reused:
        # SEC-17 for admins: the cookie leaked. Kill the whole family AND the
        # admin's access-token epoch, so a token minted moments ago dies too.
        # Commit explicitly — raising below unwinds the request-scoped session
        # and `get_session` rolls back on exception, which would discard the
        # revocation and leave the attacker's token alive.
        await session.commit()
        await revoke_user_tokens(request.app.state.auth_redis, outcome.admin_id)
        logger.critical(
            "admin refresh token replay detected for admin %s — all sessions revoked",
            outcome.admin_id,
        )
        raise HTTPException(
            status_code=401,
            detail="refresh token reuse detected",
            headers=clear_session_cookie_headers(),
        )

    if outcome.new_refresh is None or outcome.admin_id is None:
        # Covers: unknown token, expired token, and the CSRF mismatch that
        # `rotate` reports as no-outcome rather than an exception.
        raise HTTPException(status_code=401, detail="invalid refresh token")

    account = await session.get(AdminAccount, outcome.admin_id)
    if account is None or not account.is_active:
        raise HTTPException(
            status_code=403,
            detail="account disabled",
            headers=clear_session_cookie_headers(),
        )

    set_session_cookies(
        response, refresh_token=outcome.new_refresh, csrf_token=outcome.new_csrf or ""
    )
    return {
        "access_token": issue_admin_access_token(account),
        "token_type": "bearer",
        "admin": _admin_out(account),
    }


@router.post("/logout", response_model=OkLogoutOut)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _live: None = Depends(require_live_admin_refresh_session),
):
    """End the admin session: revoke every refresh token and the access epoch.

    Unlike the user `/auth/logout`, this is **not** gated by a bearer token.
    That is deliberate: the whole reason an operator presses logout is that they
    are walking away from the machine, and a session whose access token has
    already expired (15 minutes — the common case) must still be able to clear
    its cookie. Gating it would leave a live refresh cookie in the browser of a
    shared machine, which is exactly the situation logout exists to prevent.

    Being unauthenticated, anyone who can make the browser send the cookie can
    call it — and that is not a vulnerability, because the cookie *is* the
    credential and the only thing this endpoint does is destroy it. A
    CSRF-forced logout is a nuisance, not a breach.

    The CSRF token still gates the *revocation*, but not the clearing. That
    split is the useful half of the defence: a cross-site request without the
    header cannot kill the server-side session, while any request at all can
    drop the cookie from this browser — which is what the operator wanted and
    cannot be abused, since a cookie the attacker cannot read is one they cannot
    have stolen by clearing it.
    """
    raw_refresh = read_refresh_cookie(request)
    svc = AdminRefreshService(session)
    revoked = False
    admin_id = None
    if raw_refresh:
        row = await svc.lookup(raw_refresh)
        if row is not None:
            admin_id = row.admin_id
            if svc.csrf_matches(row, read_csrf_header(request)):
                # Revoke the whole family, not just the presented row: "log out"
                # means every device, and a session that only revoked its own
                # cookie would leave the operator's other tabs signed in.
                await svc.revoke_all_for_admin(row.admin_id)
                revoked = True

    clear_session_cookies(response)
    if admin_id is not None:
        # Kill access tokens already in the wild (SEC-18). Without the same key
        # `deps.assert_not_revoked` reads, this would be a no-op and the
        # operator's 15-minute access token would outlive their logout.
        await revoke_user_tokens(request.app.state.auth_redis, admin_id)
    return {"ok": True, "revoked": revoked}


def _admin_out(account) -> dict:
    """The admin identity as the console consumes it.

    No secrets, and no `totp_secret` — a field the console has no use for and
    that would be a second-factor disclosure if it ever leaked into a response.

    `admin_role` comes from the row's `admin_role` property so an unrecognised
    stored value degrades to `SUPPORT` instead of reaching the client as
    whatever string the column happens to hold.
    """
    return {
        "id": str(account.id),
        "username": account.username,
        "email": account.email,
        "full_name": account.full_name,
        "totp_enrolled": account.totp_enrolled_at is not None,
        "admin_role": account.admin_role.value,
    }


async def _issue_session(
    response: Response,
    session: AsyncSession,
    account: AdminAccount,
) -> dict:
    """Mint the access token, plant the cookie pair, and return the body.

    Every path that completes the second factor goes through here — `/totp/verify`,
    `/totp/enrol/confirm`, `/recovery` — so there is exactly one place where an
    admin session begins. That matters more than it looks: the previous version
    had three copies of `{"access_token": ..., "token_type": ..., "admin": ...}`
    and adding the refresh cookie would have meant three chances to add it to
    only two of them. The one that was missed would have produced a session that
    works for 15 minutes and then dies, which is precisely the bug being fixed.

    The refresh token is **not** in the response body. It is in an `HttpOnly`
    cookie, so it is not readable by any script on the console's origin — see
    `app/core/admin_cookies.py` for why that is the whole point.
    """
    refresh, csrf = await AdminRefreshService(session).issue(account.id)
    set_session_cookies(response, refresh_token=refresh, csrf_token=csrf)
    return {
        "access_token": issue_admin_access_token(account),
        "token_type": "bearer",
        "admin": _admin_out(account),
    }

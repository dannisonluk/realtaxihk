"""Auth API: email + password accounts, a secondary phone-OTP login, rotating
refresh tokens behind both.

The split
---------
Signing in and proving a phone number are now separate concerns with separate
endpoints:

    POST /auth/register     email + password + a *claimed* phone  -> a session
    POST /auth/login        email + password                      -> a session
    POST /auth/otp/verify   phone + code, for an ALREADY-verified number

Proving the number is what unlocks calling a taxi, and it happens on
`/identity/phone/*` (see `app/services/auth/phone_binding_service.py`) once the
caller is signed in. It is **not** a precondition for having an account, which
is what this router used to require: `POST /auth/otp/verify` both registered and
logged in, so the phone number was the only credential, and the first thing a
new user met was a demand to prove a number they had not been asked for.

Human verification gates the three doors a script tries first — register, login
and `otp/request`. **Not** `otp/verify`: the code there is already attempt-capped
at five and per-IP rate-limited, and a CAPTCHA between a person and their six
digits is the most hostile possible place to put one.

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
    PasswordForgotOut,
    TokenPairOut,
)
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.db import get_session, mark_explicit_commit
from app.core.deps import (
    Principal,
    assert_human,
    require_active_user,
    require_live_principal,
)
from app.core.exceptions import BusinessRuleError
from app.core.masking import mask_email, mask_phone
from app.core.phone import HK_PHONE_PATTERN
from app.core.security import create_access_token
from app.core.token_revocation import revoke_user_tokens
from app.models import AdminAccount, User, UserRole
from app.services.auth import phone_reverify_service as phone_reverify
from app.services.auth.account_service import (
    AccountAuthError,
    AccountAuthService,
    AccountLocked,
    AccountThrottled,
)
from app.services.auth.otp_service import OtpService
from app.services.auth.password_service import PasswordService
from app.services.auth.refresh_service import RefreshService

logger = logging.getLogger("realtaxihk.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_VERIFY_IP_RATE_LIMIT = 60
_VERIFY_IP_WINDOW_S = 60

# Long enough for a Turnstile token (Cloudflare caps them well under this) and
# short enough that the field cannot be used as a body-size amplifier.
_HUMAN_TOKEN_MAX = 4096


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # `min_length=1` only. The real policy lives in `app/core/passwords.py`, and
    # duplicating the 12-character rule here would give the client a 422 that
    # says "string too short" instead of the sentence the policy raises — and
    # two places to change when the policy moves.
    password: str = Field(min_length=1, max_length=256)
    # Required, and deliberately not verified here. See the module docstring.
    phone_e164: str = Field(pattern=HK_PHONE_PATTERN)
    human_token: str | None = Field(default=None, max_length=_HUMAN_TOKEN_MAX)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    human_token: str | None = Field(default=None, max_length=_HUMAN_TOKEN_MAX)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)


class OtpRequestIn(BaseModel):
    phone_e164: str = Field(pattern=HK_PHONE_PATTERN)
    human_token: str | None = Field(default=None, max_length=_HUMAN_TOKEN_MAX)


class OtpVerifyIn(BaseModel):
    phone_e164: str = Field(pattern=HK_PHONE_PATTERN)
    code: str = Field(pattern=r"^\d{6}$")


def _user_out(user) -> dict:
    return {
        "id": str(user.id),
        "phone_masked": mask_phone(user.phone_e164),
        "role": user.role.value,
    }


async def _revoke_access_tokens(request: Request, user_id) -> None:
    """Set the user's revocation epoch so every issued access token dies.

    The client is the app's shared per-loop one and is closed at shutdown —
    closing it here would disconnect every other request sharing it.
    """
    # SEC-18: revocation is best-effort by design. A Redis blip means a token
    # can stay valid for its 15-minute life instead of turning every request
    # into a 503; the access-token TTL is the backstop (token_revocation.py).
    failed = not await revoke_user_tokens(request.app.state.redis_factory(), user_id)
    if failed:
        logger.warning("access-token revocation failed for %s", user_id)


async def _issue_session(session: AsyncSession, user: User, *, created: bool | None = None) -> dict:
    """Mint an access token and a refresh token, and build the response body.

    Every route that signs a user in goes through here, so there is exactly one
    place that decides what a session response looks like. There used to be two
    copies — the OTP verify and the refresh — which is how `created` ended up on
    one and not the other; see `TokenPairOut`.

    `created` is omitted rather than sent as `null` when the caller has nothing
    to say, because "this route does not report creation" and "nothing was
    created" are different statements and the refresh response means the first.
    """
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh = await RefreshService(session).issue(user.id)
    # `issue()` only flushes the row; the request-scoped dependency commits
    # after the handler returns. Commit here so the refresh token handed to the
    # client always has a durable row behind it — a final commit failure after
    # the response is built would otherwise issue a token that can never rotate.
    await session.commit()
    mark_explicit_commit(session)
    body: dict = {
        "access_token": token,
        "token_type": "bearer",
        "refresh_token": refresh,
        "user": _user_out(user),
    }
    if created is not None:
        body["created"] = created
    return body


async def _run(coro, session: AsyncSession):
    """Run an account-service call, mapping its errors onto HTTP statuses.

    **Commits before raising**, for the same reason as `app.api.admin_auth._run`:
    `get_session` rolls back on exception, and every refusal here *is* an
    exception — so without this, `register_failure`'s increment and the lockout
    it triggers would be discarded, and the visible symptom would be an account
    that accepts unlimited wrong passwords.

    The 429/401 split is decided by **exception type**, never by matching the
    message text. It used to be `429 if "too many" in str(exc) else 401` in the
    admin router, which made a security-relevant status code depend on English
    wording. A lockout answers **401** rather than 429 on purpose: 429 would
    distinguish "this account is locked" from "this IP is throttled", which
    confirms the account exists and has been guessed at enough to trip the lock.

    A plain `BusinessRuleError` is committed and then **re-raised unchanged**, so
    the global handler builds the standard 400 envelope — including `details`,
    which is where the OTP resend cooldown and the attempts-remaining count live.
    """
    try:
        return await coro
    except AccountLocked as exc:
        await session.commit()
        mark_explicit_commit(session)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AccountThrottled as exc:
        await session.commit()
        mark_explicit_commit(session)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AccountAuthError as exc:
        await session.commit()
        mark_explicit_commit(session)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BusinessRuleError:
        await session.commit()
        mark_explicit_commit(session)
        raise
    except ValueError as exc:
        # `PasswordPolicyError` is a `ValueError` but not a `BusinessRuleError`,
        # so without this branch a rejected password would reach the catch-all
        # handler as a 500 — the least useful possible answer to "your password
        # is too short".
        await session.commit()
        mark_explicit_commit(session)
        raise BusinessRuleError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


@router.post("/register", status_code=201, response_model=TokenPairOut)
async def register(
    payload: RegisterIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create an account and sign in immediately.

    The phone number is recorded as a **claim**, not as proof: the new account
    can sign in and look around at once, and must still prove a number before it
    can call a taxi. That is the whole change — see
    `app/services/auth/account_service.py`.

    201 rather than 200, so a client can tell "signed up" from "signed in"
    without parsing the body.
    """
    ip = client_ip(request)
    await assert_human(payload.human_token, ip)

    svc = AccountAuthService(session, request.app.state.auth_redis)
    outcome = await _run(
        svc.register(
            email=payload.email,
            password=payload.password,
            phone_e164=payload.phone_e164,
            ip=ip,
        ),
        session,
    )
    logger.info("registered user_id=%s", outcome.user.id)
    return await _issue_session(session, outcome.user, created=outcome.created)


@router.post("/login", response_model=TokenPairOut)
async def login(
    payload: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Sign in with an email and a password.

    `created` is `false` here, not absent: the field means "did this request
    register an account", and the answer is a definite no rather than something
    this route declines to report.
    """
    ip = client_ip(request)
    await assert_human(payload.human_token, ip)

    svc = AccountAuthService(session, request.app.state.auth_redis)
    outcome = await _run(
        svc.login(email=payload.email, password=payload.password, ip=ip),
        session,
    )
    return await _issue_session(session, outcome.user, created=outcome.created)


# --------------------------------------------------------------------------- #
# Phone OTP — secondary login, and the send that feeds it
# --------------------------------------------------------------------------- #


@router.post("/otp/request", response_model=OtpRequestOut)
async def otp_request(
    payload: OtpRequestIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Send a login code to a phone number.

    The local rate limits run **before** the human check, deliberately: the check
    is an outbound HTTPS call, and putting it first would let one address make us
    perform an unbounded number of them. Cheapest gate first, expensive gate
    second, so the per-IP budget bounds the expensive one.
    """
    settings = get_settings()
    limiter = request.app.state.rate_limiter
    ip = client_ip(request)

    if not await limiter.allow(
        f"otp:ip:{ip}", settings.otp_ip_rate_limit, settings.otp_ip_window_s
    ):
        raise HTTPException(status_code=429, detail="too many OTP requests from this address")

    await assert_human(payload.human_token, ip)

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

    # No wrapper here: `OtpService` raises `BusinessRuleError` for every refusal,
    # and the global handler turns that into the standard 400 envelope *with* its
    # `details` — which is where the resend cooldown's `retry_after_seconds`
    # lives. Re-raising it as an `HTTPException` would flatten that away.
    return await OtpService(session).request_otp(payload.phone_e164)


@router.post("/otp/verify", response_model=TokenPairOut)
async def otp_verify(
    payload: OtpVerifyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Secondary login: an already-verified number plus a code sent to it.

    Kept because it is what the app shipped with, and because a driver who has
    verified a number and lost access to their email should still get in with
    the phone in their hand.

    It cannot create an account and cannot sign in an account that merely
    *claims* the number — `OtpService.verify_otp` requires
    `phone_verified_at IS NOT NULL`, so a stolen code reaches nothing the SIM did
    not already reach. That filter is the entire safety argument for keeping this
    endpoint at all.
    """
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"otp:verify:ip:{client_ip(request)}", _VERIFY_IP_RATE_LIMIT, _VERIFY_IP_WINDOW_S
    ):
        raise HTTPException(status_code=429, detail="too many verification attempts")

    auth = await OtpService(session).verify_otp(payload.phone_e164, payload.code)

    # P-4: a login OTP proves the number just as a dedicated re-verify does, so it
    # must reset the monthly clock too. Omitting this would produce the worst
    # version of the bug: users who log in every day are the most obviously
    # reachable, yet their deadline only moves if they happen to walk into the
    # re-verify screen.
    await phone_reverify.mark_verified(session, auth.user)

    return await _issue_session(session, auth.user, created=auth.created)


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #


@router.post("/refresh", response_model=TokenPairOut, response_model_exclude_none=True)
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
        mark_explicit_commit(session)
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
    # No `created`: a refresh never creates anything, and `None` here means
    # "this route does not report it" rather than "nothing was created".
    #
    # `_issue_session` omits the key, but a `response_model` does not merely
    # filter — it also **fills in defaults**, so `TokenPairOut.created = None`
    # put `"created": null` back on the wire and quietly falsified this comment
    # (and the two docstrings that repeat it). Hence `exclude_none` on the route
    # decorator. `created` is the only nullable field in `TokenPairOut` and
    # `UserOut`, so that removes exactly this one key and nothing else.
    return await _issue_session(session, user)


@router.post("/logout", response_model=OkRevokedOut)
async def logout(
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    revoked = await RefreshService(session).revoke_all_for_user(user.id)
    # SEC-18: also invalidate the access token the caller is holding (and any
    # other one already issued to this user), not just the refresh tokens.
    #
    # Order matters: the refresh-row update is still only flushed until the
    # request-scoped dependency commits. Write the DB first, then the Redis
    # epoch. Otherwise a failed final commit leaves refresh rows live while the
    # epoch is already set, and a stolen refresh token can mint a new access
    # token whose `iat` is newer than the epoch.
    await session.commit()
    mark_explicit_commit(session)
    await _revoke_access_tokens(request, user.id)
    return {"ok": True, "revoked": revoked}


# --------------------------------------------------------------------------- #
# Password — change (signed in) and reset (forgotten)
# --------------------------------------------------------------------------- #

# Per-IP budgets for the two anonymous routes. `forgot` is tighter because it is
# the one that causes an outbound email; `reset` only reads a digest.
_FORGOT_IP_RATE_LIMIT = 10
_FORGOT_IP_WINDOW_S = 3600
_RESET_IP_RATE_LIMIT = 30
_RESET_IP_WINDOW_S = 3600


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    # `min_length=1` only: the real policy is in `app/core/passwords.py`, and
    # restating the 12-character rule here would answer with pydantic's "string
    # too short" instead of the policy's sentence — the same reasoning as
    # `RegisterIn.password`.
    new_password: str = Field(min_length=1, max_length=256)


class PasswordForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    human_token: str | None = Field(default=None, max_length=_HUMAN_TOKEN_MAX)


class PasswordResetIn(BaseModel):
    # The link's token. `min_length=16` is a shape check only — the real length
    # is 43 chars from `secrets.token_urlsafe(32)`, and a short value is a 400
    # from the service rather than a wasted digest lookup.
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/password/change", response_model=OkRevokedOut)
async def password_change(
    payload: PasswordChangeIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Change the password of the signed-in account.

    Requires the **current** password even though the caller already holds a
    valid token. A stolen session must not be enough to take the account over
    permanently — otherwise "someone got my phone for two minutes" escalates
    from "they read my trips" to "they own my account".

    The response is `OkRevokedOut`, the same shape as `POST /auth/logout`, and
    `revoked` is the number of sessions killed — **including this one**. The
    client is expected to discard its tokens and sign in again; that is the
    point of the route, not a side effect. See `PasswordService.change`.
    """
    db_user = await session.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="user not found")
    revoked = await _run(
        PasswordService(session, request.app.state.auth_redis).change(
            db_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        ),
        session,
    )
    logger.info("password changed user_id=%s", user.id)
    return {"ok": True, "revoked": revoked}


@router.post("/password/forgot", response_model=PasswordForgotOut)
async def password_forgot(
    payload: PasswordForgotIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Email a reset link, if the address has an account.

    **The answer does not depend on whether it does.** This route is anonymous,
    so a response that differed would be a public "does this person have an
    account on hkfastdc?" lookup — the disclosure `POST /auth/login` refuses to
    make with its single generic message, and the one registration has to make
    and nothing else should.

    Local rate limits run before the human check, like `otp/request`: the check
    is an outbound HTTPS call, so putting it first would let one address make us
    perform an unbounded number of them.
    """
    limiter = request.app.state.rate_limiter
    ip = client_ip(request)
    if not await limiter.allow(f"pwreset:ip:{ip}", _FORGOT_IP_RATE_LIMIT, _FORGOT_IP_WINDOW_S):
        raise HTTPException(status_code=429, detail="too many reset requests from this address")

    await assert_human(payload.human_token, ip)

    return await _run(
        PasswordService(session, request.app.state.auth_redis).request_reset(payload.email, ip=ip),
        session,
    )


@router.post("/password/reset", response_model=OkRevokedOut)
async def password_reset(
    payload: PasswordResetIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Consume a reset link and set a new password.

    Anonymous by necessity — the whole point is that the caller cannot sign in.
    The token *is* the credential: 256 bits of CSPRNG, delivered only to the
    account's mailbox, stored as a digest, single-use, and expiring.

    Every failure (unknown, expired, already used) answers with one sentence, so
    someone holding a stolen token cannot learn whether it was ever real. On
    success every session is revoked, so a party who was already inside is
    ejected — which is usually why the password is being reset at all.
    """
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(
        f"pwreset:submit:ip:{client_ip(request)}", _RESET_IP_RATE_LIMIT, _RESET_IP_WINDOW_S
    ):
        raise HTTPException(status_code=429, detail="too many reset attempts")

    revoked = await _run(
        PasswordService(session, request.app.state.auth_redis).reset(
            payload.token, payload.new_password
        ),
        session,
    )
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

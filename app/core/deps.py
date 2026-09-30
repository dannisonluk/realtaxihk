"""Shared FastAPI dependencies: JWT principal + DB-backed state guards.

Stateless JWT alone cannot enforce deactivation (P0-3): user-facing endpoints
use `require_active_user` (one PK lookup per request) so a disabled account
loses access the moment is_active flips, and `require_admin` re-reads the real
user row so a phantom/stale ADMIN claim is rejected.

SEC-18: `get_current_user` also consults the per-user revocation epoch, so a
logout (or a detected refresh-token replay) invalidates access tokens that are
already in the wild instead of waiting for them to expire.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.core.token_revocation import is_token_revoked
from app.models import AccountStatus, AdminAccount, User, UserRole
from app.services import phone_reverify_service as phone_reverify

_bearer = HTTPBearer(auto_error=False)

# What `sub` points at, carried on the token so a principal can be resolved
# without guessing which table it came from.
#
# `admin_accounts` and `users` are **separate identity types** (see the
# `AdminAccount` docstring), and their ids come from independent UUID spaces. A
# bare `sub` is therefore ambiguous: the same string is a valid key for exactly
# one of the two tables and resolves to `None` in the other.
#
# Absent means `user`. That default is deliberate: every token minted before
# this claim existed was a user token, so old tokens keep working, and a user
# token that omits the claim is not misread as an admin.
SCOPE_USER = "user"
SCOPE_ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    role: UserRole
    jti: str | None = None
    issued_at: int | None = None
    # Which identity table `id` refers to. Defaults to `user` so an existing
    # call site that constructs a Principal positionally keeps its meaning.
    scope: str = SCOPE_USER

    @property
    def is_admin(self) -> bool:
        return self.scope == SCOPE_ADMIN


def principal_from_token(token: str) -> Principal:
    try:
        claims: dict[str, Any] = decode_access_token(token)
        pid = uuid.UUID(str(claims["sub"]))
        role = UserRole(str(claims.get("role", UserRole.PASSENGER.value)))
        jti = claims.get("jti")
        issued_at = claims.get("iat")
        # Anything other than the literal "admin" is treated as a user token.
        # Not `claims.get("scope") or SCOPE_USER`: a malformed value must not be
        # able to *escalate* to admin, and an allow-list of one is the cheapest
        # way to guarantee that.
        scope = SCOPE_ADMIN if claims.get("scope") == SCOPE_ADMIN else SCOPE_USER
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc
    return Principal(
        id=pid,
        role=role,
        jti=str(jti) if jti else None,
        issued_at=int(issued_at) if issued_at is not None else None,
        scope=scope,
    )


async def assert_not_revoked(request: Request, principal: Principal) -> None:
    """SEC-18: reject an access token issued before the user's revocation epoch.

    This runs on the hot path of every authenticated request, so it uses the
    app-lifetime client on `app.state.auth_redis`. Calling `redis_factory()`
    directly would have been one client (and one socket) per request — pure
    churn on a path that is already a single Redis GET.
    """
    revoked = await is_token_revoked(
        request.app.state.auth_redis, principal.id, principal.issued_at
    )
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has been revoked"
        )


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    principal = principal_from_token(creds.credentials)
    await assert_not_revoked(request, principal)
    return principal


async def require_live_principal(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """A live row exists for this token — in whichever table the token names.

    For the handful of endpoints that serve **both** identity types, of which
    `/api/v1/auth/me` is the only one: the console calls it on boot to decide
    whether the session in storage is still good, and it carries an admin token,
    while the mobile app calls it with a user token.

    `require_active_user` cannot be used for this because it hard-codes the
    `users` table, and `require_admin` cannot because it *rejects* a user token.
    Branching inside the handler would work but hides the check from the
    route-table audit in `tests/test_security_hardening.py`, which reads declared
    dependencies — so an endpoint doing its own thing in the body looks
    unguarded. Declaring it here keeps the audit's rule intact and meaningful.
    """
    if user.is_admin:
        admin = await session.get(AdminAccount, user.id)
        if admin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="admin not found")
        if not admin.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
        return user

    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    return user


async def require_active_user(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """JWT + live DB check: user exists, is_active, and matches the claim role."""
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account not found")
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    if row.role != user.role:
        # Claim no longer matches reality (e.g. demoted admin) — trust the DB.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account state changed")
    return user


async def require_admin(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """Admin must be a REAL, ACTIVE `admin_accounts` row — not just an ADMIN claim.

    The row is read from `admin_accounts`, which is where an admin identity
    actually lives: `issue_admin_access_token` sets `sub` to `admin_accounts.id`.
    Resolving it against `users` instead — as this did — looks up a UUID that is
    not in that table, so `row` is always `None` and **every** console route
    403s immediately after a successful login. The two id spaces are
    independent, so that lookup cannot be made correct by "trying both": the
    `scope` claim on the principal says which table is the right one, and an
    admin token is the only thing that can claim `admin`.

    `totp_enrolled_at` is required as well. A token can only be minted after the
    TOTP step (`_resolve_challenge` + `issue_admin_access_token`), so a row with
    no enrolment means either a hand-edited row or a token from a path that
    skipped the second factor — both of which should read as "no admin here"
    rather than being trusted.
    """
    if not user.is_admin:
        # A user token, even one carrying role=ADMIN (the legacy
        # `users.role = ADMIN` identity from `scripts/create_admin.py`), does not
        # open the console. Those are the credentials of a *different* thing.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    row = await session.get(AdminAccount, user.id)
    if row is None or not row.is_active or user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    if row.totp_enrolled_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    return user


async def require_verified_account(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """P-2: both an email and a phone must be proven before the account works.

    This is the single enforcement point for "only verified with OTP and email
    can be used". Putting it in a dependency rather than in each handler means a
    new endpoint is gated by default — the failure mode of the alternative is a
    route that forgets the check and silently lets an unverified account through.

    `UNVERIFIED` is the only blocked state. `SUSPENDED` is an admin action and is
    already covered by `is_active`; refusing it here too would produce a
    confusing "verify your email" message for a banned account.

    The error is 403 with a machine-readable `reason`, because the client has to
    distinguish "finish your profile" from "confirm your email" to route the user
    to the right screen.
    """
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    if row.account_status == AccountStatus.UNVERIFIED:
        missing = []
        if row.username is None:
            missing.append("profile")
        if row.email_verified_at is None:
            missing.append("email")
        if row.phone_verified_at is None:
            missing.append("phone")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "account verification is incomplete",
                "reason": "ACCOUNT_UNVERIFIED",
                "missing": missing,
            },
        )
    return user


async def require_phone_current(
    user: Principal = Depends(require_verified_account),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """P-4: the monthly phone re-verification, as a **soft** block.

    Attached only to the routes that *start new business* — creating an order,
    accepting one, going online. Not to reads, and not to `require_verified_account`
    itself, because the point of a soft block is precisely that the account keeps
    working: a driver mid-shift can still see their current trip, their statement
    and their profile while their number is overdue. They just cannot take on
    anything new until it is re-proven.

    That distinction is the whole reason this is a separate dependency rather than
    another branch in `require_verified_account`. Hanging it on the verified gate
    would turn a reminder into a lockout, which is the failure mode the grace
    window exists to avoid.

    The 403 carries `reason: PHONE_REVERIFY_DUE` plus the deadline, so the client
    can show "re-verify to keep accepting orders" with the date rather than a
    generic refusal.
    """
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    state = phone_reverify.evaluate(row)
    if state.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "phone re-verification is overdue",
                "reason": phone_reverify.REASON_PHONE_REVERIFY_DUE,
                **state.as_dict(),
            },
        )
    return user

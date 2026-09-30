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
from app.models import AccountStatus, User, UserRole

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    role: UserRole
    jti: str | None = None
    issued_at: int | None = None


def principal_from_token(token: str) -> Principal:
    try:
        claims: dict[str, Any] = decode_access_token(token)
        pid = uuid.UUID(str(claims["sub"]))
        role = UserRole(str(claims.get("role", UserRole.PASSENGER.value)))
        jti = claims.get("jti")
        issued_at = claims.get("iat")
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc
    return Principal(
        id=pid,
        role=role,
        jti=str(jti) if jti else None,
        issued_at=int(issued_at) if issued_at is not None else None,
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
    """Admin must be a REAL, ACTIVE admin user row — not just an ADMIN claim."""
    row = await session.get(User, user.id)
    if row is None or row.role != UserRole.ADMIN or not row.is_active or row.role != user.role:
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

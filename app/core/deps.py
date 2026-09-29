"""Shared FastAPI dependencies: JWT principal + DB-backed state guards.

Stateless JWT alone cannot enforce deactivation (P0-3): user-facing endpoints
use `require_active_user` (one PK lookup per request) so a disabled account
loses access the moment is_active flips, and `require_admin` re-reads the real
user row so a phantom/stale ADMIN claim is rejected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.models import User, UserRole

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    role: UserRole


def principal_from_token(token: str) -> Principal:
    try:
        claims = decode_access_token(token)
        pid = uuid.UUID(str(claims["sub"]))
        role = UserRole(str(claims.get("role", UserRole.PASSENGER.value)))
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc
    return Principal(id=pid, role=role)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return principal_from_token(creds.credentials)


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

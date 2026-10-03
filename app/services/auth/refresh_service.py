"""Rotating refresh tokens (P1-5) — hashed at rest, single-use rotation.

Flow: OTP login returns access (short) + refresh (long). POST /auth/refresh
rotates: the presented token is revoked and a fresh pair is issued. Logout
revokes every token of the user (device-loss friendly).

SEC-17 reuse detection: a *revoked* token being presented again means the token
leaked — either the attacker replayed a token the real user already rotated, or
the real user replayed one the attacker rotated. Either way the family is
compromised, so `rotate()` reports `reused=True` and the API layer revokes every
refresh token plus the user's access-token epoch. Previously this case was
indistinguishable from "invalid token": it returned None, the attacker kept a
working token, and the legitimate user was silently logged out.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import RefreshToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RotateOutcome:
    """Result of a rotation attempt.

    - `new_refresh` set        -> success; the caller gets a fresh pair.
    - `reused` True            -> a revoked token was replayed; revoke the family.
    - both unset/False         -> unknown or expired token; plain 401.
    """

    user_id: object | None = None
    new_refresh: str | None = None
    reused: bool = False


class RefreshService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def issue(self, user_id) -> str:
        """Create a new refresh token; returns the RAW token (shown once)."""
        raw = secrets.token_urlsafe(48)
        settings = get_settings()
        self.session.add(
            RefreshToken(
                user_id=user_id,
                token_hash=_hash(raw),
                expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
            )
        )
        await self.session.flush()
        return raw

    async def rotate(self, raw_token: str) -> RotateOutcome:
        """Single-use rotation with replay detection.

        `with_for_update()` is load-bearing: two concurrent requests presenting
        the same refresh token would otherwise both read `revoked_at IS NULL`,
        both pass the check, and both mint a new pair — defeating single-use.
        The row lock serializes them so the loser sees `revoked_at` set, which is
        then classified as a replay rather than a benign retry.
        """
        settings = get_settings()
        row = (
            (
                await self.session.execute(
                    select(RefreshToken)
                    .where(RefreshToken.token_hash == _hash(raw_token))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if row is None or row.expires_at <= _now():
            return RotateOutcome()
        if row.revoked_at is not None:
            if settings.refresh_reuse_detection:
                await self.revoke_all_for_user(row.user_id)
                return RotateOutcome(user_id=row.user_id, reused=True)
            return RotateOutcome()
        row.revoked_at = _now()
        new_raw = await self.issue(row.user_id)
        await self.session.flush()
        return RotateOutcome(user_id=row.user_id, new_refresh=new_raw)

    async def revoke_raw(self, raw_token: str) -> bool:
        row = (
            (
                await self.session.execute(
                    select(RefreshToken)
                    .where(RefreshToken.token_hash == _hash(raw_token))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = _now()
        await self.session.flush()
        return True

    async def revoke_all_for_user(self, user_id) -> int:
        res = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await self.session.flush()
        return cast("CursorResult[Any]", res).rowcount or 0

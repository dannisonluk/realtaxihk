"""Rotating refresh tokens (P1-5) — hashed at rest, single-use rotation.

Flow: OTP login returns access (short) + refresh (long). POST /auth/refresh
rotates: the presented token is revoked and a fresh pair is issued. Replay of
a rotated token fails (revoked) — clients re-OTP when that happens. Logout
revokes every token of the user (device-loss friendly).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import RefreshToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


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

    async def rotate(self, raw_token: str) -> tuple[object, str] | None:
        """Single-use rotation. Returns (user_id, new_raw) or None if invalid.

        `with_for_update()` is load-bearing: two concurrent requests presenting
        the same refresh token would otherwise both read `revoked_at IS NULL`,
        both pass the check, and both mint a new pair — defeating single-use.
        The row lock serializes them so the loser sees `revoked_at` set.
        """
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
        if row is None or row.revoked_at is not None or row.expires_at <= _now():
            return None
        row.revoked_at = _now()
        new_raw = await self.issue(row.user_id)
        await self.session.flush()
        return row.user_id, new_raw

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
        return res.rowcount or 0

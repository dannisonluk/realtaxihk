"""Background maintenance: geo-index sweeping (P0-5) + PDPO purges (P1-6).

Geo ghosts: orders that left BROADCASTING (or expired the broadcast window)
must not linger in the Redis GEO index — drivers would chase phantom orders.
Sweeper reconciles the index against the DB and auto-cancels broadcasts that
aged out (BROADCASTING -> CANCELLED is a legal transition; no penalty).

PDPO purges: OTP rows past retention; dead refresh tokens past retention.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AdminRefreshToken, Order, OrderStatus, OtpCode, RefreshToken
from app.services.geo_service import GEO_ORDERS_KEY

logger = logging.getLogger("realtaxihk.maintenance")


class MaintenanceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis):
        self.session_factory = session_factory
        self.redis = redis

    async def aclose(self) -> None:
        """Release the pooled Redis client on shutdown.

        Mirrors `RateLimiter.aclose` — without it the client is torn down when
        the event loop dies, logging an asyncio ERROR on an otherwise clean
        shutdown.
        """
        with contextlib.suppress(Exception):
            await self.redis.aclose()

    async def sweep_ghost_orders(self, max_broadcast_minutes: int) -> dict:
        """Reconcile geo:orders:active with DB truth. Returns counts."""
        now = datetime.now(UTC)
        removed_ghosts = 0
        auto_cancelled = 0

        async with self.session_factory() as session:
            # 1) Auto-cancel broadcasts older than the window.
            cutoff = now - timedelta(minutes=max_broadcast_minutes)
            stale_ids: list[uuid.UUID] = list(
                (
                    await session.execute(
                        select(Order.id).where(
                            Order.status == OrderStatus.BROADCASTING,
                            Order.created_at < cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if stale_ids:
                await session.execute(
                    update(Order)
                    .where(Order.id.in_(stale_ids))
                    .values(
                        status=OrderStatus.CANCELLED,
                        cancelled_at=now,
                        cancellation_reason="broadcast expired",
                        updated_at=now,
                    )
                )
                await session.commit()
                auto_cancelled = len(stale_ids)

            # 2) Purge index entries that no longer point at BROADCASTING rows.
            raw_ids = await self.redis.zrange(GEO_ORDERS_KEY, 0, -1)
            if raw_ids:
                uuids: list[uuid.UUID] = []
                malformed: list[str] = []
                for rid in raw_ids:
                    try:
                        uuids.append(uuid.UUID(rid))
                    except (ValueError, AttributeError, TypeError):
                        malformed.append(str(rid))
                live: set[str] = set()
                if uuids:
                    live = {
                        str(x)
                        for x in (
                            await session.execute(
                                select(Order.id).where(
                                    Order.id.in_(uuids),
                                    Order.status == OrderStatus.BROADCASTING,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    }
                ghosts = [str(r) for r in raw_ids if str(r) not in live] + malformed
                if ghosts:
                    await self.redis.zrem(GEO_ORDERS_KEY, *ghosts)
                    removed_ghosts = len(ghosts)

            # SEC-25: retire the legacy write-only driver index. Writes stopped
            # with the tracking fix; this removes whatever a previous deploy left
            # behind so Redis is not carrying unbounded stale members forever.
            with contextlib.suppress(Exception):
                await self.redis.delete("geo:drivers:online")

        if auto_cancelled or removed_ghosts:
            logger.info(
                "geo sweep: auto_cancelled=%d removed_ghosts=%d",
                auto_cancelled,
                removed_ghosts,
            )
        return {"auto_cancelled": auto_cancelled, "removed_ghosts": removed_ghosts}

    async def purge_expired_rows(
        self, retention_days_otp: int, retention_days_refresh: int
    ) -> dict:
        """PDPO: delete OTP rows and dead refresh tokens past retention.

        Covers the admin table as well as the user one. Both are strictly better
        deleted than kept and both are purged by the same rule, so folding them
        into one job means there is one retention decision to reason about
        rather than two that can drift. A missed admin table would be the worse
        of the two misses: those rows are tied to accounts that can move money.
        """
        now = datetime.now(UTC)

        async with self.session_factory() as session:
            otp_res = await session.execute(
                delete(OtpCode).where(
                    OtpCode.expires_at < now - timedelta(hours=1)
                )  # expired codes are dead weight the moment they lapse
            )
            refresh_res = await session.execute(
                delete(RefreshToken).where(
                    (RefreshToken.expires_at < now)
                    | (
                        (RefreshToken.revoked_at.is_not(None))
                        & (RefreshToken.revoked_at < now - timedelta(days=retention_days_refresh))
                    )
                )
            )
            # Same rule for admin sessions. Kept as a separate statement rather
            # than a union so the two counts stay attributable in the log — if
            # the admin figure jumps, that is worth noticing on its own.
            admin_refresh_res = await session.execute(
                delete(AdminRefreshToken).where(
                    (AdminRefreshToken.expires_at < now)
                    | (
                        (AdminRefreshToken.revoked_at.is_not(None))
                        & (
                            AdminRefreshToken.revoked_at
                            < now - timedelta(days=retention_days_refresh)
                        )
                    )
                )
            )
            # Additional retention guard: rows created long ago regardless of state.
            await session.execute(
                delete(OtpCode).where(OtpCode.created_at < now - timedelta(days=retention_days_otp))
            )
            await session.commit()
            otp_n = otp_res.rowcount or 0
            ref_n = refresh_res.rowcount or 0
            admin_ref_n = admin_refresh_res.rowcount or 0
        if otp_n or ref_n or admin_ref_n:
            logger.info("pdpo purge: otp=%d refresh=%d admin_refresh=%d", otp_n, ref_n, admin_ref_n)
        return {
            "otp_deleted": otp_n,
            "refresh_deleted": ref_n,
            "admin_refresh_deleted": admin_ref_n,
        }

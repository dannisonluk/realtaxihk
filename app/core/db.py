"""Async engine/session management + Redis client factory.

P2-7 NOTE: a process-wide Redis singleton was tried and REVERTED. Each
TestClient (and, in general, each event loop) must own its own redis-py
asyncio client: pooled connections bind to the loop that created them, and a
shared client reused across loops fails/hangs unpredictably. Production runs a
single loop, so one client per redis_factory() call costs nothing extra there.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine = None
_session_factory = None
_redis_client: Redis | None = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction-scoped session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis() -> Redis:
    """Fresh client per call (decode_responses). Loop-safe by construction."""
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def close_redis() -> None:
    """Kept for lifespan symmetry — nothing to close with per-call clients."""
    return None


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None

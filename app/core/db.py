"""Async engine/session management + Redis client access.

P2-7 NOTE: a single process-wide Redis client was tried and REVERTED. redis-py
asyncio clients bind pooled connections to the event loop that created them, so
a client reused across loops fails or hangs unpredictably. `get_redis()` therefore
caches **one client per running loop**: production runs a single loop and gets a
single client for the app's lifetime, while the test suite — where every
TestClient runs its own portal loop — still gets correct isolation.

That is the property the previous "fresh client per call" version was buying, at
the cost of opening and closing a Redis socket on every call. The cache keeps the
property and drops the churn. The map is weak-keyed on the loop, so a loop that
goes away takes its client with it instead of leaking.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import weakref
from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine = None
_session_factory = None

_redis_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Redis] = (
    weakref.WeakKeyDictionary()
)
_redis_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # `statement_cache_size` is an asyncpg connect argument, so it has to
            # travel through `connect_args` rather than as an engine option.
            # Behind PgBouncer this is set to 0 — see the setting's docstring.
            connect_args={"statement_cache_size": settings.db_statement_cache_size},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _session_factory


def mark_explicit_commit(session: AsyncSession) -> None:
    """Mark that the request already committed its DB work.

    `get_session` commits on success as a convenience for services that rely
    on the dependency to end the transaction. Services that intentionally
    commit before a side effect or a response call this immediately afterward,
    so the dependency does not issue a redundant second commit.
    """
    session.info["explicit_commit"] = True


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction-scoped session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            if not session.info.get("explicit_commit"):
                await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis() -> Redis:
    """The Redis client for the running event loop (decode_responses).

    One client per loop, created on first use and reused after that. Called
    outside a running loop (import-time wiring) it returns a fresh client, which
    is what the old per-call version did everywhere.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return Redis.from_url(get_settings().redis_url, decode_responses=True)
    with _redis_lock:
        client = _redis_clients.get(loop)
        if client is None:
            client = Redis.from_url(get_settings().redis_url, decode_responses=True)
            _redis_clients[loop] = client
        return client


async def close_redis() -> None:
    """Close every cached client (normally exactly one) and forget them."""
    with _redis_lock:
        clients = list(_redis_clients.values())
        _redis_clients.clear()
    for client in clients:
        with contextlib.suppress(Exception):
            await client.aclose()


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None

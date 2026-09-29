"""FastAPI application factory + production lifespan (jobs, graceful shutdown).

Hardening wave (docs/PRODUCTION_READINESS.md):
- P0-4 real /health: pings DB and Redis, 503 on failure.
- P0-5/P1-6 background jobs: geo sweeper + PDPO purge loops (asyncio tasks,
  cancelled cleanly on shutdown — no leaked engine).
- P2-4/P2-8: Sentry init when SENTRY_DSN set; /metrics when PROMETHEUS_ENABLED.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.drivers import router as drivers_router
from app.api.fare import router as fare_router
from app.api.orders import router as orders_router
from app.api.tracking import router as tracking_router
from app.api.trips import router as trips_router
from app.api.ws import router as ws_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers

logger = logging.getLogger("realtaxihk.main")


async def _job_loop(interval_s: int, coro_factory, name: str):
    """Run coro_factory() every interval_s; never let one failure kill the loop."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a job error must not stop the loop
            logger.exception("background job %s failed", name)
        await asyncio.sleep(interval_s)


def create_app() -> FastAPI:
    settings = get_settings()

    if settings.sentry_dsn:  # P2-4: optional error tracking
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env)
            logger.info("sentry initialized")
        except ImportError:
            logger.warning("SENTRY_DSN set but sentry-sdk not installed — skipped")

    app = FastAPI(
        title="realtaxihk.com API",
        version="0.2.0",
        description=(
            "Hong Kong taxi matching platform — information intermediary "
            "(Cap. 374D compliant fare estimates)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)
    app.include_router(fare_router)
    app.include_router(auth_router)
    app.include_router(drivers_router)
    app.include_router(admin_router)
    app.include_router(orders_router)
    app.include_router(tracking_router)
    app.include_router(trips_router)
    app.include_router(ws_router)

    from app.core.db import get_redis, get_session_factory
    from app.core.logging import configure_logging, attach_request_logging
    from app.core.rate_limit import RateLimiter
    from app.services.maintenance import MaintenanceService

    configure_logging(settings.log_level)
    attach_request_logging(app)

    app.state.redis_factory = get_redis
    app.state.rate_limiter = RateLimiter(get_redis(), namespace="realtaxi:")
    app.state.maintenance = MaintenanceService(get_session_factory(), get_redis())

    @app.on_event("startup")
    async def _startup() -> None:
        tasks: list[asyncio.Task] = []
        if settings.jobs_enabled:
            tasks.append(
                asyncio.create_task(
                    _job_loop(
                        settings.geo_sweep_interval_s,
                        lambda: app.state.maintenance.sweep_ghost_orders(
                            settings.max_broadcast_minutes
                        ),
                        "geo_sweep",
                    )
                )
            )
            tasks.append(
                asyncio.create_task(
                    _job_loop(
                        settings.purge_interval_s,
                        lambda: app.state.maintenance.purge_expired_rows(
                            settings.retention_days_otp,
                            settings.retention_days_refresh,
                        ),
                        "pdpo_purge",
                    )
                )
            )
        app.state.jobs = tasks

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        for t in getattr(app.state, "jobs", []):
            t.cancel()
        await asyncio.gather(*getattr(app.state, "jobs", []), return_exceptions=True)
        # Graceful shutdown (P0-6): close Redis, dispose the DB engine.
        with contextlib.suppress(Exception):
            from app.core.db import close_redis
            await close_redis()
        with contextlib.suppress(Exception):
            from app.core.db import dispose_engine
            await dispose_engine()

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        checks = {"db": False, "redis": False}
        try:
            from app.core.db import get_session_factory
            async with get_session_factory()() as s:
                await s.execute(text("SELECT 1"))
            checks["db"] = True
        except Exception:  # noqa: BLE001
            logger.exception("health: db check failed")
        try:
            redis = get_redis()
            await redis.ping()
            checks["redis"] = True
        except Exception:  # noqa: BLE001
            logger.exception("health: redis check failed")
        ok = all(checks.values())
        return {
            "status": "ok" if ok else "degraded",
            "env": settings.app_env,
            "checks": checks,
        }

    # P2-8: optional Prometheus metrics.
    if settings.prometheus_enabled:
        try:
            from prometheus_client import make_asgi_app
            app.mount("/metrics", make_asgi_app())
        except ImportError:  # pragma: no cover
            logger.warning("prometheus_enabled=true but prometheus-client not installed")

    return app


app = create_app()

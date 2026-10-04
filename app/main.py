"""FastAPI application factory + production lifespan (jobs, graceful shutdown).

Hardening wave (docs/PRODUCTION_READINESS.md):
- P0-4 real /health: pings DB and Redis, 503 on failure.
- P0-5/P1-6 background jobs: geo sweeper + PDPO purge loops (asyncio tasks,
  cancelled cleanly on shutdown — no leaked engine).
- P2-4/P2-8: Sentry init when SENTRY_DSN set; /metrics when PROMETHEUS_ENABLED.

Security wave (docs/SECURITY_AUDIT.md):
- SEC-09~11 body-size cap middleware;
- SEC-22 /metrics requires a bearer-style token and is not mounted without one;
- SEC-23 /health no longer advertises the environment;
- SEC-24 security headers on every response;
- SEC-14 one shared TripHub/Redis client for all sockets instead of one per socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    TokenGuardMiddleware,
)

logger = logging.getLogger("realtaxihk.main")

# The API's own version. Declared once because it is published in two places
# that must agree: the OpenAPI document (`FastAPI(version=...)`) and the Sentry
# `release`, which is what lets an error be attributed to a deploy instead of
# to "somewhere in main".
_API_VERSION = "0.2.0"


async def _job_loop(interval_s: int, coro_factory, name: str):
    """Run coro_factory() every interval_s; never let one failure kill the loop."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background job %s failed", name)
        await asyncio.sleep(interval_s)


def _start_background_jobs(app: FastAPI, settings) -> list[asyncio.Task]:
    """Start the long-running maintenance loops and return their tasks.

    Extracted from ``create_app``'s lifespan so the factory stays readable and
    the job set can be reasoned about — and tested — on its own.

    Every task is named. Those names are exactly what
    ``tests/test_hardening.py::TestLifespanBackgroundJobs`` asserts on, so a job
    that quietly stops being started becomes a test failure instead of a silent
    regression (which is how ghost-order sweeping could previously be disabled
    without any test noticing). Renaming a job here without updating that test
    is therefore a deliberate act, never an accident.
    """
    tasks: list[asyncio.Task] = []
    if not settings.jobs_enabled:
        return tasks
    tasks.append(
        asyncio.create_task(
            _job_loop(
                settings.geo_sweep_interval_s,
                lambda: app.state.maintenance.sweep_ghost_orders(settings.max_broadcast_minutes),
                "geo_sweep",
            ),
            name="geo_sweep",
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
            ),
            name="pdpo_purge",
        )
    )
    if settings.weekly_settlement_enabled:
        # Fires once at boot, then every 7 days. The per-ISO-week ledger
        # reference makes any extra run a no-op, so restarting mid-week cannot
        # double-charge a driver.
        tasks.append(
            asyncio.create_task(
                _job_loop(
                    settings.weekly_settlement_interval_s,
                    lambda: app.state.settlement.run_weekly(settings.weekly_fee_hkd),
                    "weekly_settlement",
                ),
                name="weekly_settlement",
            )
        )
    return tasks


async def _stop_background_jobs(tasks: list[asyncio.Task]) -> None:
    """Cancel every job and wait for it to unwind, so nothing is orphaned."""
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _shutdown_resources(app: FastAPI) -> None:
    """Release the Redis clients and the DB engine on shutdown (P0-6).

    Without this, uvicorn's SIGTERM leaves the connections to be reaped by
    process death, which logs an asyncio ``connection_lost()`` ERROR and can
    hold a server-side connection until TCP keepalive notices.
    """
    with contextlib.suppress(Exception):
        from app.core.db import close_redis

        await close_redis()
    # Deduped by identity: `get_redis()` caches per event loop, so the rate
    # limiter, the maintenance service, the WS hub and the auth path now share
    # ONE client; closing the same object twice would raise.
    closed: set[int] = set()
    for holder in ("rate_limiter", "maintenance", "trip_hub", "auth_redis"):
        obj = getattr(app.state, holder, None)
        if obj is None or id(obj) in closed:
            continue
        closed.add(id(obj))
        with contextlib.suppress(Exception):
            await obj.aclose()
    with contextlib.suppress(Exception):
        from app.core.db import dispose_engine

        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    if settings.sentry_dsn:  # P2-4: optional error tracking
        try:
            import sentry_sdk

            # Error monitoring only. Tracing, profiling and the Logs product
            # each need an explicit option here before they send anything, so
            # leaving them off is honest rather than an omission — an empty
            # tracing view is noise, and the quotas are per-product anyway.
            #
            # What actually reaches Sentry without further wiring is worth
            # knowing, because it is not obvious from this call:
            #
            #   * Unhandled 500s. `app/core/exceptions.py` installs a catch-all
            #     `@app.exception_handler(Exception)` that swallows the
            #     traceback and answers 500, so the exception never propagates
            #     for the ASGI integration to see. That handler logs at ERROR,
            #     and the SDK's default LoggingIntegration turns ERROR records
            #     into events — so the traceback arrives *through logging*.
            #   * Background job failures. `_job_loop` catches and
            #     `logger.exception`s them for the same reason: a loop that
            #     dies quietly is how ghost-order sweeping once went missing.
            #
            # Remove either of those log calls and the errors stop being
            # reported, with nothing else to indicate the loss.
            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.app_env,
                release=f"realtaxihk-api@{_API_VERSION}",
                # PDPO. This service handles HK phone numbers and licence
                # photos, and Sentry is a third party in another jurisdiction.
                # The traceback and the log line are what make an error
                # actionable; the request body is not, and it is the one part
                # that carries personal data — an OTP request body is a phone
                # number. The SDK default is `medium`, which would ship it.
                max_request_body_size="never",
                # Also the default, but stated so that a future change to it is
                # a visible decision rather than a silent one.
                send_default_pii=False,
            )
            logger.info("sentry initialized")
        except ImportError:
            logger.warning("SENTRY_DSN set but sentry-sdk not installed — skipped")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup + shutdown in one place (the modern replacement for the
        deprecated ``@app.on_event``, which this module's docstring already
        promised).

        The two halves are deliberately thin: the job set lives in
        ``_start_background_jobs`` and the teardown in ``_shutdown_resources``,
        so this stays a readable orchestration of the application lifecycle
        rather than a place where 80 lines of maintenance wiring hide.
        """
        tasks = _start_background_jobs(app, settings)
        app.state.jobs = tasks
        try:
            yield
        finally:
            await _stop_background_jobs(tasks)
            await _shutdown_resources(app)

    app = FastAPI(
        title="hkfastdc.com API",
        version=_API_VERSION,
        lifespan=lifespan,
        description=(
            "Hong Kong taxi matching platform — information intermediary "
            "(Cap. 374D compliant fare estimates)."
        ),
    )
    register_exception_handlers(app)
    # The whole route table, assembled in one auditable place. Registration
    # order is load-bearing (three admin routers must precede the platform
    # one) -- see `app/api/router.py`.
    app.include_router(api_router)

    from app.core.db import get_redis, get_session_factory
    from app.core.logging import attach_request_logging, configure_logging
    from app.core.rate_limit import RateLimiter
    from app.services.infra.maintenance import MaintenanceService
    from app.services.ledger.settlement_service import SettlementService
    from app.services.order.trip_service import ConnectionRegistry, TripHub

    configure_logging(settings.log_level)
    attach_request_logging(app)

    app.state.redis_factory = get_redis
    app.state.rate_limiter = RateLimiter(get_redis(), namespace=settings.redis_key_namespace)
    # SEC-18: the revocation check sits on the hot path of EVERY authenticated
    # request. It used to call `get_redis()` per request, which opened a socket
    # each time (measured at 2s before the 127.0.0.1 fix, ~3ms after — churn
    # either way). `get_redis()` now caches per event loop, so this is the same
    # client the rest of the app uses, closed with the others on shutdown.
    app.state.auth_redis = get_redis()
    app.state.maintenance = MaintenanceService(get_session_factory(), get_redis())
    app.state.settlement = SettlementService(get_session_factory())
    # SEC-14: ONE hub holding ONE Redis client. Previously each socket built its
    # own client + pubsub, so a single account could push Redis's client count
    # toward `maxclients` and take the whole platform down with it.
    app.state.trip_hub = TripHub(get_redis())
    # Both caps are PER PROCESS - this is the only place either is consumed, and
    # `ConnectionRegistry` keeps its counters in memory. N uvicorn workers
    # therefore enforce each cap N times over, so the platform-wide ceiling is
    # `ws_max_connections_total * API_WORKERS`. Prod pins `--workers 1` for
    # exactly this reason (the three-part route to raising it - redo the DB pool
    # arithmetic, move the registry to Redis, then raise the worker count - is
    # written out in docker-compose.prod.yml). Raising the worker count without
    # reading that is a silent correctness regression, not a capacity win.
    app.state.ws_registry = ConnectionRegistry(
        max_per_user=settings.ws_max_connections_per_user,
        max_total=settings.ws_max_connections_total,
    )

    @app.get("/health", tags=["ops"])
    async def health(response: Response) -> dict:
        checks = {"db": False, "redis": False}
        try:
            from app.core.db import get_session_factory

            async with get_session_factory()() as s:
                await s.execute(text("SELECT 1"))
            checks["db"] = True
        except Exception:
            logger.exception("health: db check failed")
        try:
            # The client is cached per loop and closed at shutdown; closing it
            # here would disconnect it on every readiness probe.
            await get_redis().ping()
            checks["redis"] = True
        except Exception:
            logger.exception("health: redis check failed")
        ok = all(checks.values())
        if not ok:
            # P0-4: the STATUS CODE is what a load balancer or readiness probe
            # acts on. Answering 200 with a "degraded" body keeps traffic
            # flowing to a node whose DB/Redis is gone.
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        # SEC-23: the environment is NOT advertised. `env: dev` told an attacker
        # exactly which fixed OTP code to try (SEC-01).
        return {
            "status": "ok" if ok else "degraded",
            "checks": checks,
        }

    # P2-8 + SEC-22: optional Prometheus metrics, token-gated. Not mounted at all
    # without a token — an unauthenticated /metrics leaks route shapes, traffic
    # volumes and error rates to anyone who can reach the port.
    if settings.prometheus_enabled:
        if not settings.metrics_token:
            logger.warning(
                "PROMETHEUS_ENABLED=true but METRICS_TOKEN is empty — /metrics NOT mounted"
            )
        else:
            try:
                from prometheus_client import make_asgi_app

                app.mount("/metrics", TokenGuardMiddleware(make_asgi_app(), settings.metrics_token))
            except ImportError:  # pragma: no cover
                logger.warning("prometheus_enabled=true but prometheus-client not installed")

    # Middleware order (last added = outermost):
    #   CORS -> SecurityHeaders -> BodySizeLimit -> routes
    # CORS is outermost so even a 413 from the body cap carries CORS headers;
    # SecurityHeaders sits outside the body cap so the 413 gets hardened too.
    if settings.max_request_body_bytes > 0:
        app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    if settings.security_headers_enabled:
        app.add_middleware(
            SecurityHeadersMiddleware,
            hsts_max_age_s=settings.hsts_max_age_s,
            enable_hsts=settings.app_env == "prod",
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    return app


app = create_app()

"""FastAPI application factory. Real DB/Redis wiring lands with Module A/B."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="realtaxihk.com API",
        version="0.1.0",
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

    # shared infra handles (lazy connections; created once per app)
    from app.core.db import get_redis
    from app.core.rate_limit import RateLimiter

    app.state.redis_factory = get_redis
    app.state.rate_limiter = RateLimiter(get_redis(), namespace="realtaxi:")

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()

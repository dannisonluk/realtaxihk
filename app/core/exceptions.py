"""Standardized error payloads: { code, message, details }."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class BusinessRuleError(ValueError):
    """Domain rule violation surfaced as HTTP 400."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


def _error(code: str, message: str, details: Any = None) -> dict:
    return {"code": code, "message": message, "details": details or {}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # SEC-09: pydantic attaches the offending value as `input` on every error,
        # and echoing it made a failed request cost as much to answer as it did to
        # send — 20k bogus `tunnels` came back as a 100 KB body, and the unbounded
        # version of exactly this was the 32.8 MB response SEC-09 is about. It also
        # reflected caller-supplied values straight back out, which is how a bad
        # payload containing a credential ends up in a response and in every log
        # that records one. Clients use `type` / `loc` / `msg`; keep those.
        errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=_error(
                "VALIDATION_ERROR",
                "Request validation failed.",
                {"errors": jsonable_encoder(errors, custom_encoder={Decimal: str})},
            ),
        )

    @app.exception_handler(BusinessRuleError)
    async def on_business_rule(request: Request, exc: BusinessRuleError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_error("BUSINESS_RULE_VIOLATION", exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(request: Request, exc: StarletteHTTPException):
        code_map = {
            status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
            status.HTTP_404_NOT_FOUND: "NOT_FOUND",
            status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
            status.HTTP_403_FORBIDDEN: "FORBIDDEN",
            status.HTTP_409_CONFLICT: "CONFLICT",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "PAYLOAD_TOO_LARGE",
            status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
            status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
        }
        # A `detail` that is already a structured dict goes to `details`, not
        # into `message`. `str()` on it produced Python repr — `{'reason':
        # 'PHONE_REVERIFY_DUE'}` with single quotes and braces — which is not
        # JSON, so a client could not parse the machine-readable reason the
        # guards go to the trouble of attaching. Every structured refusal
        # (ACCOUNT_UNVERIFIED, PHONE_REVERIFY_DUE, the map in
        # `app.api.geo._reject`) was flattened this way.
        if isinstance(exc.detail, dict):
            payload = dict(exc.detail)
            message = payload.pop("message", "Request failed.")
            details: Any = payload
        else:
            message = str(exc.detail)
            details = None
        # SEC: `WWW-Authenticate` and `Retry-After` are part of the contract for
        # 401/429/503 — dropping them makes clients (and load balancers) behave
        # worse than the status code alone implies.
        return JSONResponse(
            status_code=exc.status_code,
            content=_error(code_map.get(exc.status_code, "HTTP_ERROR"), message, details),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error("INTERNAL_ERROR", "Internal server error."),
        )

"""Standardized error payloads: { code, message, details }."""

from __future__ import annotations

import logging
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
        # Every status this API can actually raise must appear here. A miss is
        # not cosmetic: the client branches on `code`, so an unmapped status
        # arrives as the generic `HTTP_ERROR` and the console cannot tell one
        # refusal from another (see `admin-web/web/src/api/client.ts`, whose
        # `CODE` map is the other half of this contract).
        #
        # 423 is load-bearing for P4 — `DEPOSIT_INSUFFICIENT` (the driver's
        # deposit is in arrears) is deliberately *not* a 403, because it is a
        # state the driver can fix by topping up, whereas 403 is a permission
        # verdict. Leaving it unmapped would have made the deposit gate and the
        # "you are permanently banned" case indistinguishable to the client.
        code_map = {
            status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
            status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
            status.HTTP_403_FORBIDDEN: "FORBIDDEN",
            status.HTTP_404_NOT_FOUND: "NOT_FOUND",
            status.HTTP_409_CONFLICT: "CONFLICT",
            status.HTTP_422_UNPROCESSABLE_CONTENT: "UNPROCESSABLE_ENTITY",
            status.HTTP_423_LOCKED: "LOCKED",
            # `HTTP_413_REQUEST_ENTITY_TOO_LARGE` is deprecated in the pinned
            # Starlette (it warns on every use, which drowns real warnings in
            # the test output) and renames to this. The literal is kept in the
            # value so the wire contract does not move with a library rename.
            status.HTTP_413_CONTENT_TOO_LARGE: "PAYLOAD_TOO_LARGE",
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
            # `reason` is the field the guards actually populate
            # (`{"reason": "DEPOSIT_INSUFFICIENT", ...}`, `{"reason": "COOLDOWN"}`),
            # and they do not send a `message`. Without this fallback every such
            # refusal reached the operator as the literal string "Request
            # failed." while the machine-readable reason sat in `details` — a
            # console that shows `message` therefore showed nothing useful for
            # exactly the refusals that most need explaining. Promoting `reason`
            # keeps the payload shape unchanged for clients that read `details`.
            message = payload.pop("message", None) or payload.get("reason") or "Request failed."
            details: Any = payload
        else:
            message = str(exc.detail)
            details = None
        # SEC: `WWW-Authenticate` and `Retry-After` are part of the contract for
        # 401/429/503 — dropping them makes clients (and load balancers) behave
        # worse than the status code alone implies.
        #
        # `Set-Cookie` gets special handling: this fresh response is the *only*
        # thing the client sees, so a handler that needs to expire a cookie
        # while refusing a request (a replayed refresh token, a disabled
        # account) must be able to attach it here. A `dict[str, str]` cannot
        # express two cookies — the natural comma-fold is unsafe, because the
        # `expires` attribute contains a comma of its own, and a client
        # splitting on it mangles both cookies. So a caller may pass the value
        # as a **list**, which is emitted as one `Set-Cookie` header per entry.
        headers = dict(getattr(exc, "headers", None) or {})
        set_cookies = headers.pop("set-cookie", None)
        response = JSONResponse(
            status_code=exc.status_code,
            content=_error(code_map.get(exc.status_code, "HTTP_ERROR"), message, details),
            headers=headers or None,
        )
        if set_cookies:
            values = [set_cookies] if isinstance(set_cookies, str) else list(set_cookies)
            for value in values:
                response.raw_headers.append((b"set-cookie", value.encode("latin-1")))
        return response

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        # Logged, because this handler is the *only* place an unexpected
        # exception is observed: it swallows the traceback and answers 500, so
        # without this the root cause of every 500 exists nowhere but the
        # access log's status code. The handler used to be silent, which made
        # a 500 indistinguishable from a proactive 500 the app raised itself.
        #
        # The response body stays generic on purpose — an exception message can
        # carry a connection string, a file path or a SQL fragment, and this is
        # the one path where the text is completely unvetted.
        logging.getLogger("realtaxihk.unhandled").exception(
            "unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error("INTERNAL_ERROR", "Internal server error."),
        )

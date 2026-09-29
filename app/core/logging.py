"""Production logging: JSON to stdout + request IDs + token-scrubbed access logs.

P0-4: structured JSON logs (grepable, shippable); P1-1: the WS ?token= query
never reaches any log line — scrubbed defensively even if uvicorn's own
access logger is active.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_TOKEN_RE = re.compile(r"([?&])token=[^&\s\"']+")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "+ms": int(record.msecs),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class StripTokenQueryFilter(logging.Filter):
    """Rewrite ?token=<jwt> out of any string log args (defence in depth)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            scrubbed = (
                tuple(
                    _TOKEN_RE.sub(r"\1token=REDACTED", a) if isinstance(a, str) else a
                    for a in record.args
                )
                if isinstance(record.args, tuple)
                else record.args
            )
            record.args = scrubbed
            if isinstance(record.msg, str):
                record.msg = _TOKEN_RE.sub(r"\1token=REDACTED", record.msg)
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(StripTokenQueryFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Uvicorn's loggers may already have handlers (when the app is imported
    # after uvicorn configured logging) — unify them onto our JSON handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False

    # Our own access logger (middleware below) — root handler applies.
    logging.getLogger("realtaxihk.access").setLevel(logging.INFO)


def configure_logging(level: str = "INFO") -> None:
    """Alias used by main.py."""
    setup_logging(level)


def attach_request_logging(app) -> None:
    """Request-ID + scrubbed access logs as ASGI middleware (P0-4 / P1-1)."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class RequestLogMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            rid = new_request_id()
            request_id_var.set(rid)
            start = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                logging.getLogger("realtaxihk.access").exception(
                    "unhandled error %s %s", request.method, request.url.path
                )
                raise
            finally:
                ms = (time.perf_counter() - start) * 1000
            logging.getLogger("realtaxihk.access").info(
                "%s %s -> %s %.1fms",
                request.method,
                request.url.path,
                getattr(response, "status_code", "?"),
                ms,
            )
            response.headers["X-Request-ID"] = rid
            return response

    app.add_middleware(RequestLogMiddleware)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]

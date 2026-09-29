"""Production logging: JSON to stdout + request IDs + token-scrubbed access logs.

P0-4: structured JSON logs (grepable, shippable); P1-1: the WS ?token= query
never reaches any log line — scrubbed defensively even if uvicorn's own
access logger is active.

Scrubbing happens in two places on purpose:
- `JsonFormatter.format` scrubs the FINAL rendered message, which is the only
  layer that also catches `logger.info("...?token=%s", jwt)` — the token there
  arrives as an argument with no `token=` prefix of its own.
- `StripTokenQueryFilter` scrubs the raw record before rendering, so a handler
  that is not ours still never sees the token.
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

# A JWT is base64url, so `%` can safely be excluded from the value class. That
# keeps `%s` / `%(name)s` placeholders in a format string intact — matching
# across one would delete the placeholder and make getMessage() raise
# "not all arguments converted during string formatting".
_TOKEN_RE = re.compile(r"([?&])token=[^&\s\"'%]+")


def scrub_tokens(value):
    """Recursively redact ?token=… inside str / tuple / dict log payloads."""
    if isinstance(value, str):
        return _TOKEN_RE.sub(r"\1token=REDACTED", value)
    if isinstance(value, tuple):
        return tuple(scrub_tokens(v) for v in value)
    if isinstance(value, dict):
        return {k: scrub_tokens(v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "+ms": int(record.msecs),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub_tokens(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = scrub_tokens(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class StripTokenQueryFilter(logging.Filter):
    """Redact ?token=<jwt> before the record is rendered (defence in depth).

    `args` is scrubbed whenever present; `msg` is scrubbed only when the record
    carries no args — i.e. when the message already *is* the final text.
    Rewriting a `%s` format string would strip its placeholder and break
    `getMessage()`, so that case is left to the formatter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = scrub_tokens(record.args)
        elif isinstance(record.msg, str):
            record.msg = scrub_tokens(record.msg)
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

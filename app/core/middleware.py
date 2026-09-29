"""Pure-ASGI middleware: request-body cap (SEC-09~11) + security headers (SEC-24).

Both are implemented at the ASGI level rather than with `BaseHTTPMiddleware`
because that wrapper buffers and re-streams the body, which is precisely what a
body-size limit must not do (and it is a known source of subtle
cancellation/`StreamingResponse` bugs).
"""

from __future__ import annotations

import hmac
import json

_JSON = b"application/json"


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", _JSON),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """Raised out of the receive wrapper; caught by the middleware."""


class BodySizeLimitMiddleware:
    """Reject oversized request bodies with 413 before they are parsed.

    SEC-09~11: the API had no body cap at all, so a 63 MB JSON document was
    accepted (HTTP 200) and fed to the JSON parser. Two enforcement points:

    1. a declared `Content-Length` over the cap is rejected without reading a byte;
    2. for chunked/undeclared bodies the receive stream is metered, so the cap
       holds even when the client lies by omission.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = None
        for key, value in scope.get("headers") or ():
            if key == b"content-length":
                try:
                    declared = int(value)
                except (TypeError, ValueError):
                    await _send_json(
                        send,
                        400,
                        {"code": "BAD_REQUEST", "message": "invalid Content-Length", "details": {}},
                    )
                    return
                break
        if declared is not None and declared > self.max_bytes:
            await _send_json(
                send,
                413,
                {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"request body exceeds {self.max_bytes} bytes",
                    "details": {"max_bytes": self.max_bytes},
                },
            )
            return

        received = 0

        async def metered_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, metered_receive, send)
        except _BodyTooLarge:
            await _send_json(
                send,
                413,
                {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"request body exceeds {self.max_bytes} bytes",
                    "details": {"max_bytes": self.max_bytes},
                },
            )


# Applied to every response. CSP is the only one that could break the built-in
# Swagger/ReDoc UI (inline scripts), so it is scoped to the API prefix below.
_BASE_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
)

_API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class TokenGuardMiddleware:
    """Gate an ASGI sub-app (e.g. the Prometheus exporter) behind a static token.

    SEC-22: `app.mount("/metrics", make_asgi_app())` had no dependency at all, so
    anyone who could reach the port could read route shapes, traffic volumes and
    error rates. Prometheus itself supports a bearer token on the scrape config,
    so a shared secret is the least-friction fix.
    """

    def __init__(self, app, token: str, header: str = "x-metrics-token"):
        self.app = app
        self.token = token
        self.header = header.lower().encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = b""
        for key, value in scope.get("headers") or ():
            if key.lower() == self.header:
                supplied = value
                break
        if not self.token or not hmac.compare_digest(supplied, self.token.encode()):
            await _send_json(
                send,
                401,
                {"code": "UNAUTHORIZED", "message": "metrics token required", "details": {}},
            )
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Attach hardening headers to every HTTP response.

    SEC-24: the API returned none of these. HSTS is emitted only for prod — on a
    localhost dev host it would pin `localhost` to HTTPS in the developer's
    browser and break local work.
    """

    def __init__(self, app, *, hsts_max_age_s: int, enable_hsts: bool, api_prefix: str = "/api/"):
        self.app = app
        self.hsts_max_age_s = hsts_max_age_s
        self.enable_hsts = enable_hsts
        self.api_prefix = api_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_api = scope.get("path", "").startswith(self.api_prefix)
        extra = list(_BASE_HEADERS)
        if self.enable_hsts:
            extra.append((b"strict-transport-security", f"max-age={self.hsts_max_age_s}".encode()))
        if is_api:
            extra.append((b"content-security-policy", _API_CSP))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra if k not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

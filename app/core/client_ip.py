"""Resolve the caller's address for rate limiting and audit (SEC-07).

This exists as one function rather than the five byte-identical copies that were
in `app/api/{auth,admin_auth,fare,identity,licence}.py`. The duplication was not
a style problem — it was a correctness one, because *this specific function* is
the thing SEC-07 is about.

What SEC-07 was: the original implementation took
`x_forwarded_for.split(",")[0]` — the **leftmost** element, which is exactly the
part a client controls. nginx's `$proxy_add_x_forwarded_for` *appends* the real
peer, so even a correct deployment left the attacker-controlled prefix in
position 0, and every IP-scoped rate limit (OTP request, OTP verify, fare
estimate, username check, email send, licence upload, admin login) was
bypassable by rotating one header.

What it is now: X-Forwarded-For is ignored entirely unless
`TRUSTED_PROXY_COUNT > 0`, and when it is read the hops are counted **from the
right** — `[-trusted_proxy_count]` is the peer as seen by the outermost trusted
proxy, which no client can forge. The standard single-proxy deployment
(`docker-compose.prod.yml` sets `TRUSTED_PROXY_COUNT=1`) sends exactly one hop,
so the exact-count case must also be accepted or every caller collapses onto the
proxy's address. When the header is absent or has too few hops, the code falls
back to `X-Real-IP`, which nginx overwrites with the peer it saw, then to the
transport peer.

Five copies meant five places to get that wrong, and any one of them drifting
back to `[0]` would have silently reopened the hole for that endpoint alone.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    """The caller's address, with X-Forwarded-For trusted only behind a proxy.

    Falls back to the transport peer when there is no trusted proxy configured or
    no header present, and to `"unknown"` when the request has no client at all
    (which happens under some ASGI test transports). It never raises: every
    caller is building a rate-limit key or an audit row, and both are better off
    with the string `"unknown"` than with a 500.
    """
    settings = get_settings()
    if settings.trusted_proxy_count > 0:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            # Trust the header when it has at least as many hops as the
            # configured trusted proxies. With exactly that many hops the
            # leftmost value was appended by the outermost trusted proxy (the
            # standard single-nginx case); with more, count back from the right.
            # Fewer hops than the configured chain means an upstream proxy did
            # not forward one, so the leftmost value is attacker-controlled.
            if len(hops) >= settings.trusted_proxy_count:
                # Count from the right; see the module docstring.
                idx = len(hops) - settings.trusted_proxy_count
                return hops[idx]
        # nginx overwrites X-Real-IP with the peer it saw, so unlike an
        # X-Forwarded-For prefix this header cannot be minted by the client.
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"

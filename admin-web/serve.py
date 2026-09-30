#!/usr/bin/env python3
"""Serve the admin console locally.

    python admin-web/serve.py            # http://127.0.0.1:3000
    python admin-web/serve.py --port 3001

Why not `python -m http.server`: it needs `--directory`, serves any file it finds
under the root without a MIME allow-list, and sends no security headers. This is a
50-line handler instead, and it pins the three things that actually matter for an
ES-module app:

* **The right MIME type.** A module served as `text/plain` is refused by the
  browser with an opaque console error, which is a bad first-run experience.
* **`no-store`.** Otherwise a stale `app.js` survives a reload and the console
  appears not to have changed.
* **Path containment.** A request for `../../.env` is answered 404, not with the
  file. The console is served from a directory that sits inside the API
  repository, so this is not hypothetical.

Port 3000 is the default because it is already in the backend's `cors_origins`
(`app/core/config.py`), so the API accepts requests from it without a config
change. Serving this from any other port needs that list extended.

**Reverse proxy.** Any request whose path starts with `/api/` (or `/health`) is
forwarded to the API at `--api-target` instead of being served from disk. Open
the console with `?api=same-origin` and the whole app is one origin: every call is
a *simple* request, so there is no CORS preflight at all. That is worth having
where the API is not directly reachable from the browser (a tunnel, a container
network, a production reverse proxy), and it removes a whole class of
preflight-related failure. The proxy is hand-rolled on `http.client` so this file
keeps its single dependency.
"""

from __future__ import annotations

import argparse
import http.client
import os
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Per-request proxy timings, off by default.
#
# When a browser run stalls it is not obvious whether the console or the API is
# the one not answering. `SERVE_PROXY_TIMEOUT` bounds the upstream wait (a
# shorter value fails a wedged upstream faster, freeing the browser socket
# sooner); `SERVE_PROXY_TIMING=1` prints `proxy <path> <status> <ms>` for every
# forwarded request so the stall can be localised without a packet capture.
PROXY_TIMING = os.environ.get("SERVE_PROXY_TIMING") == "1"

ROOT = Path(__file__).resolve().parent

# Which directory is actually served.
#
# Two builds live side by side: the legacy hand-rolled ES-module bundle at
# `admin-web/` (`js/`, `styles.css`) and the Vite + React rewrite at
# `admin-web/web/dist`. They are kept separate so the legacy console keeps
# working until the port is verified; `--dist` selects the rewrite.
#
# Set by `main()` from the flag, and read by the handler, because
# `SimpleHTTPRequestHandler` is constructed by the server rather than by us.
SERVE_ROOT: Path = ROOT

# Paths forwarded to the API rather than served from disk. Prefix-matched.
PROXY_PREFIXES = ("/api/", "/health")

# How long to wait on the upstream before giving up and returning 502.
#
# Not 120s. On this sandbox a uvicorn process intermittently accepts a request
# and then never answers it, and the upstream block was measured at 92s before
# the server logged a 200 the client had already stopped waiting for
# (`admin-web/README.md`). A short bound turns that into a fast 502, which
# `api.js` already retries on a **fresh** connection — and because the stall is
# per-connection, a fresh connection is a real second chance, not a repeat of the
# same failure.
#
# Why 3s and not 10s: the browser opens at most **six** sockets to this origin.
# While six forwarded requests are parked on a wedged upstream, those six sockets
# are held for the full timeout, and the *next* request — even a static file —
# cannot get one; the page hangs in the browser's socket pool rather than in this
# server. At 10s a route that fires six calls at once parks the whole pool for
# ten seconds and the run stalls after a handful of routes. 3s is still an order
# of magnitude above the p100 for a healthy request (<200ms), so it never fires
# on a working API, and a wedged batch frees the pool three times sooner.
# Override with `SERVE_PROXY_TIMEOUT` (seconds) when a slow upstream is expected.
UPSTREAM_TIMEOUT_S = float(os.environ.get("SERVE_PROXY_TIMEOUT", "3"))

# A module loaded with the wrong MIME type is blocked outright, so the types that
# matter are listed rather than left to the platform's `mimetypes` table.
EXTRA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SERVE_ROOT), **kwargs)

    # The allow-list above wins; anything else falls back to the platform table.
    def guess_type(self, path):
        suffix = Path(path).suffix.lower()
        if suffix in EXTRA_TYPES:
            return EXTRA_TYPES[suffix]
        return super().guess_type(path)

    def end_headers(self):
        # The console is a development tool that is edited and reloaded; a cached
        # bundle would make a change look like it did nothing.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The app needs no window features and no top-level navigation.
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def send_head(self):
        """Refuse anything that resolves outside the served directory."""
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        try:
            resolved = (SERVE_ROOT / raw.lstrip("/")).resolve()
        except (OSError, ValueError):
            self.send_error(400, "Bad path")
            return None
        if SERVE_ROOT not in resolved.parents and resolved != SERVE_ROOT:
            self.send_error(404, "Not found")
            return None
        if not resolved.exists():
            # SPA fallback. The console hash-routes, so a deep link never reaches
            # the server — but a *refresh* on the Vite dev-server path, or an
            # asset reference that moved between builds, should still land on the
            # app rather than a bare 404 with no way back.
            fallback = SERVE_ROOT / "index.html"
            if fallback.is_file() and not raw.startswith(("/api/", "/health")):
                self.path = "/index.html"
        return super().send_head()

    def log_message(self, fmt, *args):
        # Quieter than the default, and no client address — this can end up in a
        # terminal that is being shared.
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # -- reverse proxy ----------------------------------------------------

    def _is_proxied(self) -> bool:
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        return any(raw == prefix.rstrip("/") or raw.startswith(prefix) for prefix in PROXY_PREFIXES)

    def _proxy(self) -> None:
        """Forward this request to the API and write the response back.

        `Host` is rewritten to the target so the API's routing and logs see a
        coherent value, and hop-by-hop headers are dropped. The response is
        buffered so its `Content-Length` can be sent: without one, a keep-alive
        client cannot tell where the body ends.

        A **fresh** upstream connection per request, deliberately. A pooled one
        was tried and reverted: this sandbox intermittently accepts a connection
        and then never answers on it, and a shared connection serialises every
        later request behind the one that is stuck. Per-request connections fail
        independently, so the burst degrades to a few 502s the client retries
        rather than the whole console hanging.
        """
        scheme, host, port = self.server.api_target  # type: ignore[attr-defined]
        factory = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length", "connection", "accept-encoding")
        }
        headers["Host"] = f"{host}:{port}" if port else host
        if body is not None:
            headers["Content-Length"] = str(len(body))

        started = time.monotonic()
        try:
            conn = factory(host, port, timeout=UPSTREAM_TIMEOUT_S)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
            finally:
                conn.close()
        except (OSError, http.client.HTTPException) as exc:
            # A 502 is the right answer here: the *proxy* is up, its upstream is
            # not answering. `api.js` retries GETs on 502/503/504, so a stalled
            # upstream becomes a retry on a fresh connection rather than a page
            # that hangs until the browser gives up.
            if PROXY_TIMING:
                print(
                    f"  proxy {self.command} {self.path} -> 502 "
                    f"{int((time.monotonic() - started) * 1000)}ms ({exc})",
                    flush=True,
                )
            self.send_error(502, f"API unreachable: {exc}")
            return

        if PROXY_TIMING:
            print(
                f"  proxy {self.command} {self.path} -> {response.status} "
                f"{int((time.monotonic() - started) * 1000)}ms",
                flush=True,
            )

        self.send_response(response.status)
        for key, value in response.getheaders():
            # `Content-Length` is recomputed from the buffered payload;
            # `Transfer-Encoding`/`Connection` belong to this connection, not the
            # one being proxied.
            if key.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _reject_static(self) -> None:
        """Static assets are read-only: non-GET falls through to the API path."""
        self.send_error(405, "Method not allowed")

    def _dispatch(self, static_handler):
        if self._is_proxied():
            self._proxy()
        else:
            static_handler()

    def do_GET(self):
        self._dispatch(super().do_GET)

    def do_POST(self):
        self._dispatch(self._reject_static)

    def do_PATCH(self):
        self._dispatch(self._reject_static)

    def do_PUT(self):
        self._dispatch(self._reject_static)

    def do_DELETE(self):
        self._dispatch(self._reject_static)

    def do_OPTIONS(self):
        # Same-origin means no preflight to answer; forward one if it arrives
        # anyway so the API's CORS layer is the single source of that answer.
        self._dispatch(self._reject_static)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the RealTaxi HK admin console.")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--api-target",
        default="http://127.0.0.1:8000",
        help="Upstream API for /api/* and /health (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--dist",
        action="store_true",
        help="Serve the Vite + React build (admin-web/web/dist) instead of the legacy bundle.",
    )
    args = parser.parse_args()

    global SERVE_ROOT
    if args.dist:
        SERVE_ROOT = ROOT / "web" / "dist"
        if not (SERVE_ROOT / "index.html").is_file():
            raise SystemExit(
                f"{SERVE_ROOT} has no index.html — run `npm run build` in admin-web/web first."
            )
    if not (SERVE_ROOT / "index.html").is_file():
        raise SystemExit(f"index.html not found in {SERVE_ROOT}")

    target = urlsplit(args.api_target)
    if not target.hostname:
        raise SystemExit(f"--api-target must be an absolute URL, got {args.api_target!r}")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    # Stashed on the server because `SimpleHTTPRequestHandler` is constructed by
    # the server itself, so there is no constructor to thread it through.
    server.api_target = (target.scheme or "http", target.hostname, target.port)  # type: ignore[attr-defined]

    print(f"admin console  ->  http://{args.host}:{args.port}")
    print(f"serving        <-  {SERVE_ROOT}")
    print(f"api proxied    ->  {args.api_target}   (use ?api=same-origin to route through it)")
    print("press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

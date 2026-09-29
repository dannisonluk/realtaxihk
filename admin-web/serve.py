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
"""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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
        super().__init__(*args, directory=str(ROOT), **kwargs)

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
            resolved = (ROOT / raw.lstrip("/")).resolve()
        except (OSError, ValueError):
            self.send_error(400, "Bad path")
            return None
        if ROOT not in resolved.parents and resolved != ROOT:
            self.send_error(404, "Not found")
            return None
        return super().send_head()

    def log_message(self, fmt, *args):
        # Quieter than the default, and no client address — this can end up in a
        # terminal that is being shared.
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the RealTaxi HK admin console.")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if not (ROOT / "index.html").is_file():
        raise SystemExit(f"index.html not found in {ROOT}")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"admin console  ->  http://{args.host}:{args.port}")
    print(f"serving        <-  {ROOT}")
    print("api expected on http://127.0.0.1:8000 (override with ?api=<base url>)")
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

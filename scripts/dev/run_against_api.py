"""Start the API, run a Python probe against it, tear down. One process.

`serve_and_run_browser.py` only runs Node scripts; backend-level probes (the
concurrency test that shows whether a connection reset is uvicorn's doing or the
console proxy's) need the API alone. This is that harness.

    python scripts/dev/run_against_api.py <probe.py>
"""

import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from _root import REPO_ROOT

ROOT = str(REPO_ROOT)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

PROBE = sys.argv[1]

# `asyncio` (SelectorEventLoop) is the one that survives concurrency here.
# Python 3.12 on Windows defaults to `ProactorEventLoop`, and under it uvicorn
# intermittently accepts a TCP connection and then never serves it — measured
# at ~1 in 10 requests timing out for 30s while the other 9 answer in 0.2s
# (`scripts/verify/probe_concurrency.py`). Selector does not show it. Override with
# `LOOP=<name> python scripts/dev/run_against_api.py ...`.
LOOP = os.environ.get("LOOP", "asyncio")

# Extra uvicorn flags for the experiment being run, e.g.
#   UVICORN_EXTRA="--http h11 --timeout-keep-alive 1"
UVICORN_EXTRA = os.environ.get("UVICORN_EXTRA", "").split()


def port_open(host, port, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
    except OSError:
        return False
    else:
        return True
    finally:
        s.close()


if port_open("127.0.0.1", 8000):
    print("[FATAL] something is already LISTENING on 127.0.0.1:8000 — kill it first", flush=True)
    sys.exit(2)

env = dict(os.environ)
env["PYTHONPATH"] = ROOT
env["PYTHONUNBUFFERED"] = "1"

api = subprocess.Popen(
    [
        PY,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--loop",
        LOOP,
        # SEC-31: never let uvicorn rewrite scope["client"] from X-Forwarded-For.
        # It trusts 127.0.0.1 by default, which makes the IP rate limit spoofable.
        "--no-proxy-headers",
        # SEC-32: keep the WS token out of uvicorn's access log.
        "--no-access-log",
        *UVICORN_EXTRA,
    ],
    cwd=ROOT,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

try:
    deadline = time.time() + 120
    while time.time() < deadline and not port_open("127.0.0.1", 8000):
        time.sleep(0.5)
    if not port_open("127.0.0.1", 8000):
        print("[FAIL] api never opened", flush=True)
        sys.exit(1)
    print("[ok] api up", flush=True)

    # Warm it, then let the probe run against a warm server. Same reasoning as
    # the browser harness: the first request pays the pool warm-up.
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=90) as resp:
        print(f"[warm] health -> {resp.status}", flush=True)

    print("=== PROBE ===", flush=True)
    v = subprocess.run(
        [PY, PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    print(v.stdout, flush=True)
    if v.stderr:
        print("=== STDERR ===", flush=True)
        print(v.stderr[-4000:], flush=True)
    print(f"=== EXIT: {v.returncode} ===", flush=True)

    print("--- api log tail ---", flush=True)
    api.terminate()
    # `Popen.stdout` is `IO[str] | None` to the checker — it cannot see that this
    # script always passes `stdout=PIPE`. Binding a local is what makes the
    # narrowing stick; an attribute read back later is not narrowed. An explicit
    # raise rather than an `assert`, because ruff's S101 bans asserts outside the
    # files that carry a documented exemption.
    api_out = api.stdout
    if api_out is None:
        raise RuntimeError("the api child is spawned with stdout=PIPE")
    print(api_out.read()[-5000:], flush=True)
    sys.exit(v.returncode)
finally:
    with contextlib.suppress(Exception):
        api.kill()

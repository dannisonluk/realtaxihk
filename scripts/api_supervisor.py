#!/usr/bin/env python3
"""Keep a uvicorn process alive in a sandbox that wedges it.

Why this exists — measured, not assumed:

    A uvicorn process on this machine serves a bounded number of requests and
    then stops answering, permanently. The bound is ~9 and it is *per process
    lifetime*, not per connection and not time-based: the same wall shows up
    whether the client opens 15 fresh connections or reuses one. A bare
    `http.server` on the same port answers 20/20, so it is uvicorn-specific, not
    the OS loopback stack. It is also intermittent — the identical probe reports
    9/20 on one run and 15/15 the next — so it tracks machine load (a security
    agent inspecting loopback traffic is the prime suspect).

    See `admin-web/README.md` -> "the sandbox drops one response in a burst".

Neither the console nor the API can code around a server that has stopped
answering. What *does* work is a fresh process: a newly started uvicorn serves a
fresh budget every time. So this supervisor watches its child and restarts it the
moment it stops answering, which turns a permanent wedge into a ~2s blip.

Usage:

    python scripts/api_supervisor.py --port 8000 --check-interval 2

The supervisor owns the port for the life of the process. It exits when it
receives SIGTERM/SIGINT, taking its child (and the child's children) with it.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def probe(port: int, timeout: float = 3.0) -> bool:
    """True if `/health` answers within `timeout`."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            resp.read()
            return resp.status == 200
    except (OSError, urllib.error.URLError):
        return False


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
    except OSError:
        return False
    else:
        return True
    finally:
        s.close()


def kill_tree(p: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    with contextlib.suppress(Exception):
        p.kill()


def start(port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [
            PY,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--loop",
            "asyncio",
            # SEC-31: never trust X-Forwarded-For from loopback.
            "--no-proxy-headers",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart uvicorn when it wedges.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--check-interval",
        type=float,
        default=2.0,
        help="Seconds between health checks (default: 2)",
    )
    parser.add_argument(
        "--max-probe",
        type=float,
        default=6.0,
        help="Give a probe this long before calling the API wedged (default: 6)",
    )
    args = parser.parse_args()

    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"

    proc = start(args.port, env)
    print(f"[supervisor] api on :{args.port} pid={proc.pid}", flush=True)

    deadline = time.time() + 60
    while time.time() < deadline and not port_open(args.port):
        time.sleep(0.5)
    if not port_open(args.port):
        print("[supervisor] api never opened — giving up", flush=True)
        kill_tree(proc)
        return 1
    print(f"[supervisor] api up on :{args.port}", flush=True)

    restarts = 0
    try:
        while True:
            time.sleep(args.check_interval)
            if proc.poll() is not None:
                print("[supervisor] child exited — restarting", flush=True)
            elif not probe(args.port, args.max_probe):
                print("[supervisor] health check failed — restarting wedged api", flush=True)
            else:
                continue

            kill_tree(proc)
            proc = start(args.port, env)
            restarts += 1
            deadline = time.time() + 60
            while time.time() < deadline and not port_open(args.port):
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
            print(f"[supervisor] restart #{restarts} done, pid={proc.pid}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        kill_tree(proc)
        print(f"[supervisor] stopped after {restarts} restart(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

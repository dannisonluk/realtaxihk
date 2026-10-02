"""API + console + an arbitrary Node script, all inside one process.

Background servers do not survive between tool calls in this sandbox, so the
only reliable way to run a browser script against the real stack is to hold
every subprocess open in a single Python process.
"""

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from _root import REPO_ROOT

ROOT = str(REPO_ROOT)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

# `playwright` is NOT a top-level package on this machine. It ships *nested*
# inside the Playwright CLI — `<node>/versions/<ver>/node_modules/@playwright/
# cli/node_modules/playwright` — and `node/workspace/node_modules` is empty, so
# pointing NODE_PATH there (as this script used to) makes `import 'playwright'`
# fail with MODULE_NOT_FOUND before the browser ever opens. Resolve it instead.
_NODE_VERSIONS = Path(r"C:\Users\user\.workbuddy-ai\binaries\node\versions")
_PLAYWRIGHT_PARENT = next(
    (
        p
        for p in sorted(_NODE_VERSIONS.glob("*/node_modules/@playwright/cli/node_modules"))
        if (p / "playwright").is_dir()
    ),
    None,
)
if _PLAYWRIGHT_PARENT is None:
    print(
        "[FATAL] could not find playwright under "
        f"{_NODE_VERSIONS}\\*\\node_modules\\@playwright\\cli\\node_modules",
        flush=True,
    )
    sys.exit(2)
NODE_WS = str(_PLAYWRIGHT_PARENT)
NODE = str(_PLAYWRIGHT_PARENT.parents[3] / "node.exe")

# The script plus its own flags, given as one shell-style string:
#   python scripts/dev/serve_and_run_browser.py "admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081"
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else r"admin-web\tool\.ui-check\debug_login.mjs"
SCRIPT_ARGV = SCRIPT.split()


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


def wait_port(host, port, seconds=90, label=""):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_open(host, port):
            print(f"[ok] {label} on {host}:{port}", flush=True)
            return True
        time.sleep(0.5)
    print(f"[FAIL] {label} never opened {host}:{port}", flush=True)
    return False


procs = []


def kill_tree(p):
    """Kill `p` and every descendant.

    `terminate()` on the direct child is not enough when that child is `uvicorn
    --workers N` or `serve.py`'s threading server: the *grandchildren* are the
    ones holding :8000 open, and they outlive a plain terminate. A leftover
    listener on 8000 is not a harmless leak — the next run connects to a
    half-dead server, and the symptom is a 90s stall or a 502 that reads like a
    product bug. `taskkill /T` is the only reliable tree-kill on Windows.
    """
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    with contextlib.suppress(Exception):
        p.kill()


def spawn(cmd, label, env=None, stream=False):
    """Start a child server.

    `stream=True` lets the child write straight to this process's stdout instead
    of a pipe. The console's per-request proxy log is the only live signal of
    where a stalled run is stuck, and a pipe would hold it until the very end —
    exactly when it is no longer useful. The API keeps a pipe (its log is drained
    and tailed once at the end; nothing needs it live).
    """
    print(f"[spawn] {label}", flush=True)
    p = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=None if stream else subprocess.PIPE,
        stderr=subprocess.STDOUT if not stream else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    procs.append((label, p))
    return p


env = dict(os.environ)
env["PYTHONPATH"] = ROOT
env["PYTHONUNBUFFERED"] = "1"
# Print a line per proxied request (`proxy GET /api/v1/... -> 200 42ms`). This is
# the only view into where a stall actually is when the browser stops making
# progress: it distinguishes "the console is holding sockets on a wedged
# upstream" from "the console is idle and the browser is stuck", which the run
# log otherwise cannot tell apart.
env["SERVE_PROXY_TIMING"] = "1"

# Refuse to start on top of a leftover server.
#
# A previous run that was killed (tool timeout, Ctrl-C) does not take its
# children with it, and those orphans keep LISTENING. `wait_port` then sees the
# port open, assumes *its own* server came up, and the browser talks to a stale
# API and a stale console bundle — which presents as a login that silently
# bounces back, i.e. it looks exactly like a product bug. Fail loudly instead.
for label, port in (("api", 8000), ("console", 8081)):
    if port_open("127.0.0.1", port):
        print(
            f"[FATAL] something is already LISTENING on 127.0.0.1:{port} — "
            f"an orphaned {label} from a killed run. Kill it first:\n"
            f'  netstat -ano | findstr ":{port}"   then   taskkill /F /PID <pid>',
            flush=True,
        )
        sys.exit(2)

try:
    # The API runs under `api_supervisor.py`, not directly.
    #
    # A uvicorn process on this machine stops answering after a bounded number of
    # requests (~9) and never recovers, and the wedge is present for essentially
    # the whole run once Chromium is in the process tree — so a browser run hits
    # it every time. A *fresh* process serves a fresh budget, so the supervisor
    # health-checks the API and restarts it the moment it stops answering, which
    # turns a run-killing wedge into a ~2s blip. The supervisor also carries the
    # SEC-31 `--no-proxy-headers` flag for its uvicorn child.
    api = spawn(
        [PY, "scripts/dev/api_supervisor.py", "--port", "8000", "--check-interval", "2"],
        "api",
        env,
    )
    if not wait_port("127.0.0.1", 8000, 120, "api"):
        print(api.stdout.read()[-4000:], flush=True)
        sys.exit(1)

    # `--dist` is not optional here. Without it `serve.py` serves the *legacy*
    # hand-rolled bundle, and `verify_ui.mjs` deliberately refuses to run against
    # it (no `#login-username`, different asset paths) — so the one-command flow
    # this script exists to provide would abort on every run.
    serve = spawn(
        [PY, "admin-web/serve.py", "--port", "8081", "--dist"], "console", env, stream=True
    )
    if not wait_port("127.0.0.1", 8081, 30, "console"):
        sys.exit(1)

    # No API warm-up, deliberately.
    #
    # Warming used to matter: the first DB-backed request after a cold start
    # measured 17.5s while later ones were <100ms. That cost is now documented
    # as a transport artefact (see `admin-web/README.md`), and this machine
    # silently drops one connection out of every burst to a uvicorn process, so
    # *every* connection spent warming is one the browser cannot use. The
    # verifier's own sign-in wait (90s) absorbs a cold start instead.

    node_env = dict(env)
    node_env["NODE_PATH"] = NODE_WS
    # A `.py` script is run with the project interpreter: the browser scripts are
    # Node, but a probe that only needs HTTP (to isolate the proxy from the
    # browser) is easier to write in Python, and it has to run inside this
    # process to keep the servers alive.
    if SCRIPT_ARGV[0].endswith(".py"):
        runner, run_env, label = [PY, *SCRIPT_ARGV], env, "PYTHON SCRIPT"
    else:
        runner, run_env, label = [NODE, *SCRIPT_ARGV], node_env, "NODE SCRIPT"
    print(f"=== {label} ===", flush=True)
    # 1200s. The verifier retries a route up to three times and every failed
    # attempt costs its full timeout (20s navigation + 15-40s wait), and sign-in
    # alone is allowed 90s for a cold API — so a run on a bad transport day can
    # legitimately take many minutes. A healthy run finishes in ~100s; the script
    # exits the moment it is done either way, so this is only the ceiling.
    v = subprocess.run(
        runner,
        cwd=ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
        check=False,
    )
    print(v.stdout, flush=True)
    if v.stderr:
        print("=== STDERR ===", flush=True)
        print(v.stderr[-4000:], flush=True)
    print(f"=== EXIT: {v.returncode} ===", flush=True)

    print("--- api log tail ---", flush=True)
    kill_tree(api)
    print(api.stdout.read()[-6000:], flush=True)

    # The console's proxy log already streamed live (it inherits this stdout), so
    # there is nothing left to drain — just stop it.
    kill_tree(serve)
    sys.exit(v.returncode)
finally:
    for _label, p in procs:
        kill_tree(p)
    time.sleep(1.5)
    for _label, p in procs:
        with contextlib.suppress(Exception):
            p.kill()

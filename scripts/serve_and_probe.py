"""Spawn uvicorn detached, wait for health, then exit (server keeps running)."""

import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
tmp = PROJECT_ROOT / ".tmp"
tmp.mkdir(exist_ok=True)
# handle stays open on purpose: the child process inherits it as stdout
log = open(tmp / "uvicorn.log", "w", encoding="utf-8")  # noqa: SIM115

config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, log_level="info")
server = uvicorn.Server(config)
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
    stdout=log,
    stderr=subprocess.STDOUT,
    cwd=str(PROJECT_ROOT),
)
for _ in range(40):
    try:
        r = httpx.get("http://127.0.0.1:8000/health", timeout=1)
        print("READY:", r.status_code, r.json())
        sys.exit(0)
    except Exception:
        time.sleep(0.5)
print("FAILED to start; log:", (tmp / "uvicorn.log").read_text(encoding="utf-8")[-800:])
sys.exit(1)

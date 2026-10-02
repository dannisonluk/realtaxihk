"""Single-threaded: 20 requests, one after another, no concurrency at all.

If this is 20/20 clean, the stall is specific to *concurrent* connections in the
sandbox — which pins the failure to the environment rather than to uvicorn, the
console, or any project code.
"""

import time
import urllib.request

URL = "http://127.0.0.1:8000/health"
N = 20

bad = 0
for i in range(N):
    t0 = time.monotonic()
    headers = {"Accept": "application/json", "Connection": "close"}
    try:
        req = urllib.request.Request(URL, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print(f"  req {i:2d}  status={resp.status}  {time.monotonic() - t0:.2f}s", flush=True)
    except Exception as exc:
        bad += 1
        elapsed = time.monotonic() - t0
        print(f"  req {i:2d}  FAILED {type(exc).__name__} after {elapsed:.2f}s", flush=True)

print(f"--- {N - bad}/{N} ok, {bad} failed ---", flush=True)

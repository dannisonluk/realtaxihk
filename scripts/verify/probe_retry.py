"""Does a single retry clear the stall?

If one stuck connection out of N is the sandbox's connection interposition, a
client that retries on a connect/read timeout gets a clean result every time.
That is worth knowing because it decides the fix: a retry in the client (a real
robustness improvement) vs. an environment change.
"""

import concurrent.futures
import time
import urllib.error
import urllib.request

N = 12
URL = "http://127.0.0.1:8000/health"


def one(i, attempt):
    t0 = time.monotonic()
    headers = {"Accept": "application/json", "Connection": "close"}
    req = urllib.request.Request(URL, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()
    return resp.status, time.monotonic() - t0


def hit(i, retries=3):
    last = None
    for attempt in range(retries):
        try:
            status, elapsed = one(i, attempt)
        except Exception as exc:
            last = f"{type(exc).__name__}"
        else:
            tag = "ok" if attempt == 0 else f"ok-after-{attempt}-retry"
            return (i, status, f"{elapsed:.2f}s", tag)
    return (i, None, "-", f"FAILED after {retries}: {last}")


with concurrent.futures.ThreadPoolExecutor(max_workers=N) as pool:
    for row in pool.map(hit, range(N)):
        print(f"  req {row[0]:2d}  status={row[1]}  {row[2]}  {row[3]}", flush=True)

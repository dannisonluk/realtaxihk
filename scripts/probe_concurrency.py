"""Fire N concurrent requests at the API directly, no proxy, no browser.

If this shows resets, the reset is uvicorn-on-Windows, not the console proxy.
"""

import concurrent.futures
import time
import urllib.error
import urllib.request

N = 10
URL = "http://127.0.0.1:8000/health"


def hit(i):
    t0 = time.monotonic()
    headers = {"Accept": "application/json", "Connection": "close"}
    try:
        req = urllib.request.Request(URL, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            return (i, resp.status, f"{time.monotonic() - t0:.2f}s", "")
    except Exception as exc:
        return (i, None, f"{time.monotonic() - t0:.2f}s", f"{type(exc).__name__}: {exc}")


with concurrent.futures.ThreadPoolExecutor(max_workers=N) as pool:
    for row in pool.map(hit, range(N)):
        print(f"  req {row[0]:2d}  status={row[1]}  {row[2]}  {row[3]}", flush=True)

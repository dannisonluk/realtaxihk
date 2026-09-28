"""Stop the detached uvicorn on :8000 (find PID via netstat, then kill)."""
import re
import subprocess
import sys

out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
pids = set()
for line in out.splitlines():
    if ":8000" in line and "LISTENING" in line:
        m = re.search(r"(\d+)\s*$", line)
        if m:
            pids.add(m.group(1))
if not pids:
    print("no listener on :8000")
    sys.exit(0)
for pid in pids:
    r = subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True)
    print(f"kill {pid}:", r.returncode, (r.stdout or r.stderr).strip())

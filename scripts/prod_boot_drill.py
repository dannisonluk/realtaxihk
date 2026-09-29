"""Prod boot fail-fast drill: APP_ENV=prod with dev-grade secrets must REFUSE to boot.

P0-2 acceptance: the startup validator raises instead of starting the server.
Docker db/redis run (healthcheck would otherwise not be reached anyway).
Exit 0 = validator refused (correct), exit 1 = app booted (BAD).
"""

import os
import subprocess
import sys

env = os.environ.copy()
env.update(
    {
        "APP_ENV": "prod",
        "JWT_SECRET_KEY": "dev-secret-change-me",  # the dev default — must be rejected
        "POSTGRES_PASSWORD": "change-me-dev",  # dev default — must be rejected
        "SENTRY_DSN": "",
    }
)

r = subprocess.run(
    [sys.executable, "-c", "from app.main import app; print('BOOTED')"],
    env=env,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
)

out = r.stdout + r.stderr
ok = r.returncode != 0 and ("JWT_SECRET" in out or "dev" in out.lower())
print("stdout:", r.stdout[:300])
print("stderr:", r.stderr[:600])
print("RESULT:", "FAIL-FAST OK" if ok else "BAD — booted with dev secrets!")
sys.exit(0 if ok else 1)

"""Prod boot fail-fast drill (SEC-01~05 acceptance).

Each case boots the real app in a subprocess and asserts the startup validator
either REFUSES (unsafe config) or BOOTS (safe config). The subprocess runs with
the project root on PYTHONPATH but its cwd in a scratch directory, so a
developer's `.env` cannot mask the case under test — which is exactly the
scenario the audit found: a container image with no `.env` at all.

Exit 0 = every case behaved correctly, exit 1 = at least one did not.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STRONG_SECRET = "Zx9q7Lm2Wp4Rt6Yk8Bn3Vc5Hj1Sd0Fg6"  # noqa: S105 — drill fixture, not a credential
COMMITTED_DEV_SECRET = "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef"  # noqa: S105 — the value we must prove is rejected

# Settings we always control, so an inherited value cannot leak into a case.
MANAGED = ("APP_ENV", "ALLOW_DEV_OTP", "JWT_SECRET_KEY", "POSTGRES_PASSWORD")

CASES = [
    (
        "prod + the committed dev JWT secret",
        {"APP_ENV": "prod", "JWT_SECRET_KEY": COMMITTED_DEV_SECRET, "POSTGRES_PASSWORD": "real"},
        False,
        "JWT_SECRET_KEY",
    ),
    (
        "prod + dev database password",
        {"APP_ENV": "prod", "JWT_SECRET_KEY": STRONG_SECRET, "POSTGRES_PASSWORD": "change-me-dev"},
        False,
        "POSTGRES_PASSWORD",
    ),
    (
        "prod + ALLOW_DEV_OTP",
        {
            "APP_ENV": "prod",
            "JWT_SECRET_KEY": STRONG_SECRET,
            "POSTGRES_PASSWORD": "real",
            "ALLOW_DEV_OTP": "true",
        },
        False,
        "ALLOW_DEV_OTP",
    ),
    (
        "APP_ENV not set at all",
        {"JWT_SECRET_KEY": STRONG_SECRET},
        False,
        "APP_ENV",
    ),
    (
        "APP_ENV=production (not whitelisted)",
        {"APP_ENV": "production", "JWT_SECRET_KEY": STRONG_SECRET},
        False,
        "must be one of",
    ),
    (
        "low-entropy JWT secret (64 identical chars)",
        {"APP_ENV": "dev", "JWT_SECRET_KEY": "x" * 64},
        False,
        "entropy",
    ),
    (
        "prod with proper secrets",
        {"APP_ENV": "prod", "JWT_SECRET_KEY": STRONG_SECRET, "POSTGRES_PASSWORD": "real"},
        True,
        "BOOTED",
    ),
]


def run_case(case_env: dict) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in MANAGED}
    env.update(case_env)
    env["PYTHONPATH"] = str(ROOT)
    env["SENTRY_DSN"] = ""
    with tempfile.TemporaryDirectory() as scratch:
        r = subprocess.run(
            [sys.executable, "-c", "from app.main import app; print('BOOTED')"],
            env=env,
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    failures = []
    for name, case_env, should_boot, expect_substring in CASES:
        code, out = run_case(case_env)
        booted = code == 0 and "BOOTED" in out
        if booted != should_boot:
            failures.append(f"{name}: expected booted={should_boot}, got booted={booted}")
            verdict = "BAD"
        elif expect_substring not in out:
            failures.append(f"{name}: message did not mention {expect_substring!r}")
            verdict = "BAD"
        else:
            verdict = "ok"
        expectation = "boot" if should_boot else "refuse"
        print(f"[{verdict:>3}] {name} -> expected {expectation}, exit={code}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print(f"\nAll {len(CASES)} config fail-fast cases behaved correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

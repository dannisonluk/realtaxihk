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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT as ROOT

STRONG_SECRET = "Zx9q7Lm2Wp4Rt6Yk8Bn3Vc5Hj1Sd0Fg6"  # noqa: S105 — drill fixture, not a credential
COMMITTED_DEV_SECRET = "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef"  # noqa: S105 — the value we must prove is rejected

# Settings we always control, so an inherited value cannot leak into a case.
# SMTP_HOST/SMTP_FROM/PUBLIC_BASE_URL are in here for the same reason as the
# secrets: if one were inherited from the ambient environment, a "refuse" case
# could pass for the wrong reason (or a boot case could be masked).
#
# TRUSTED_PROXY_COUNT and CORS_ORIGINS joined on 2026-10-12 when the prod
# validators for them landed. They have to be listed *and* present in `_PROD_OK`,
# because two of the new cases work by *removing* a key from it -- and a key
# inherited from the ambient environment would make "unset" case pass vacuously.
#
# TURNSTILE_SECRET_KEY joined with the human-verification change: it is the one
# prod setting whose absence fails *open* at runtime (`DisabledHumanVerifier`
# allows everything with a warning), so the "unset" case is the only thing
# standing between us and a deploy that looks healthy while being unprotected.
MANAGED = (
    "APP_ENV",
    "ALLOW_DEV_OTP",
    "JWT_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "SMTP_HOST",
    "SMTP_FROM",
    "PUBLIC_BASE_URL",
    "TRUSTED_PROXY_COUNT",
    "CORS_ORIGINS",
    "TURNSTILE_SECRET_KEY",
)

# The complete set a prod deploy needs to boot. Kept as one dict so the
# negative cases below can be expressed as "this, but with X removed/changed",
# which is what keeps them honest: when a new required setting is added, the
# happy path fails loudly instead of the drill quietly testing less.
#
# TRUSTED_PROXY_COUNT=1 is what the documented deploy uses (nginx in front).
# CORS_ORIGINS must be https:// -- a wildcard or a plain-http entry is refused.
# TURNSTILE_SECRET_KEY is the site's *secret* key (the site key is public and is
# not validated here) -- any non-empty value satisfies the validator.
_PROD_OK = {
    "APP_ENV": "prod",
    "JWT_SECRET_KEY": STRONG_SECRET,
    "POSTGRES_PASSWORD": "real",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_FROM": "no-reply@example.com",
    "PUBLIC_BASE_URL": "https://api.hkfastdc.com",
    "TRUSTED_PROXY_COUNT": "1",
    "CORS_ORIGINS": '["https://console.hkfastdc.com"]',
    "TURNSTILE_SECRET_KEY": "0x4AAAAAAA-real-turnstile-secret",
}

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
        # SEC-07: 0 means X-Forwarded-For is ignored entirely, so behind nginx
        # every caller shares one rate-limit bucket. Refusing is the fix; this
        # case is what proves the refusal survives a refactor.
        "prod + TRUSTED_PROXY_COUNT=0 (behind nginx)",
        {**_PROD_OK, "TRUSTED_PROXY_COUNT": "0"},
        False,
        "TRUSTED_PROXY_COUNT",
    ),
    (
        "prod + CORS_ORIGINS unset",
        {k: v for k, v in _PROD_OK.items() if k != "CORS_ORIGINS"},
        False,
        "CORS_ORIGINS",
    ),
    (
        # A wildcard origin with credentials is the combination browsers reject
        # anyway; a deploy that "worked" in a browser test would be one where
        # CORS was silently broken for the real console.
        "prod + CORS_ORIGINS=*",
        {**_PROD_OK, "CORS_ORIGINS": '["*"]'},
        False,
        "wildcard",
    ),
    (
        "prod + plain-http CORS origin",
        {**_PROD_OK, "CORS_ORIGINS": '["http://console.hkfastdc.com"]'},
        False,
        "https://",
    ),
    (
        # The only fail-closed check whose runtime counterpart fails *open*:
        # an unconfigured secret boots happily and allows every script through,
        # so a deploy that forgot the key is indistinguishable from a protected
        # one until the WhatsApp bill arrives.
        "prod + TURNSTILE_SECRET_KEY unset",
        {k: v for k, v in _PROD_OK.items() if k != "TURNSTILE_SECRET_KEY"},
        False,
        "TURNSTILE_SECRET_KEY",
    ),
    (
        # "Proper secrets" means *everything* the prod validator demands — not
        # just the three it demanded when this drill was written. The P-2 SMTP /
        # PUBLIC_BASE_URL checks were added later and this case silently rotted:
        # nothing runs the drill in CI (`.github/workflows/ci.yml` is ruff +
        # pytest only), so it sat at 6/7 without anyone seeing it.
        "prod with proper secrets",
        _PROD_OK,
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

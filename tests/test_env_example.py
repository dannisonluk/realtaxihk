"""`.env.example` must list every setting the app or the compose stack reads.

A documentation-drift guard, not a functional test. It exists because the file
had silently fallen 18 settings behind `Settings` — the whole R2 and SMTP blocks
among them, which are precisely the ones a deployer cannot guess and which
`docs/DEPLOYMENT_REQUIREMENTS.md` describes as required in production. Nothing
failed; the example just quietly stopped being true.

Two consumers read this file, and the distinction matters:

  * the app, through pydantic `Settings` (field names, case-insensitive);
  * `docker-compose.yml`, through `${VAR}` substitution — `REDIS_PASSWORD`,
    `REDIS_PORT` and `APP_BIND_IP` are compose variables, NOT app settings, so
    comparing against `Settings` alone reports them as stale when they are
    perfectly live.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"

_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_COMPOSE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


def _example_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(stripped)
        if match:
            keys.add(match.group(1).lower())
    return keys


def _compose_vars() -> set[str]:
    return {m.lower() for m in _COMPOSE_VAR.findall(COMPOSE.read_text(encoding="utf-8"))}


def test_every_setting_is_documented_in_the_example():
    """A setting with no line in `.env.example` is one nobody can discover."""
    missing = sorted(set(Settings.model_fields) - _example_keys())
    assert missing == [], f"declared in app/core/config.py but absent from .env.example: {missing}"


def test_every_compose_variable_is_documented_in_the_example():
    missing = sorted(_compose_vars() - _example_keys())
    assert missing == [], (
        f"docker-compose.yml substitutes these, but .env.example omits them: {missing}"
    )


def test_the_example_has_no_keys_nothing_reads():
    """A stale line is worse than a missing one: it looks like configuration."""
    known = set(Settings.model_fields) | _compose_vars()
    stale = sorted(_example_keys() - known)
    assert stale == [], f".env.example lists keys that no code or compose file reads: {stale}"

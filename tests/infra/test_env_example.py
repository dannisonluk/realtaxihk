"""`.env.example` must list every setting the app or the compose stack reads.

A documentation-drift guard, not a functional test. It exists because the file
had silently fallen 18 settings behind `Settings` — the whole R2 and SMTP blocks
among them, which are precisely the ones a deployer cannot guess and which
`docs/DEPLOYMENT_REQUIREMENTS.md` describes as required in production. Nothing
failed; the example just quietly stopped being true.

Two consumers read this file, and the distinction matters:

  * the app, through pydantic `Settings` (field names, case-insensitive);
  * the compose stack, through `${VAR}` substitution — `REDIS_PASSWORD`,
    `REDIS_PORT` and `APP_BIND_IP` are compose variables, NOT app settings, so
    comparing against `Settings` alone reports them as stale when they are
    perfectly live.

Both compose files count, not just the base one. `docker-compose.prod.yml` is an
overlay that introduces variables of its own (`API_WORKERS`,
`PG_MAX_CONNECTIONS`), and those are exactly the ones a deployer has to set —
so leaving the overlay out of this guard would reproduce the original drift on
the file that matters most.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.prod.yml")

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


def _strip_comments(text: str) -> str:
    """Drop YAML comments before looking for substitutions.

    Without this, a comment that *documents* the syntax — e.g. the note in
    `docker-compose.prod.yml` explaining that compose resolves `${VAR:-default}`
    from the host environment — is read as a variable the deploy actually needs,
    and the guard fails on prose. The overlay is heavily commented, so this is
    not a hypothetical.

    A `#` opens a comment at the start of a line or after whitespace, but not
    inside a quoted scalar, so the scan tracks quote state instead of splitting
    on the character.
    """
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw
        quote: str | None = None
        for index, char in enumerate(raw):
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#" and (index == 0 or raw[index - 1].isspace()):
                line = raw[:index]
                break
        kept.append(line)
    return "\n".join(kept)


def _compose_vars() -> set[str]:
    found: set[str] = set()
    for compose in COMPOSE_FILES:
        text = _strip_comments(compose.read_text(encoding="utf-8"))
        found |= {m.lower() for m in _COMPOSE_VAR.findall(text)}
    return found


def test_every_setting_is_documented_in_the_example():
    """A setting with no line in `.env.example` is one nobody can discover."""
    missing = sorted(set(Settings.model_fields) - _example_keys())
    assert missing == [], f"declared in app/core/config.py but absent from .env.example: {missing}"


def test_every_compose_variable_is_documented_in_the_example():
    missing = sorted(_compose_vars() - _example_keys())
    assert missing == [], (
        f"the compose files substitute these, but .env.example omits them: {missing}"
    )


def test_the_example_has_no_keys_nothing_reads():
    """A stale line is worse than a missing one: it looks like configuration."""
    known = set(Settings.model_fields) | _compose_vars()
    stale = sorted(_example_keys() - known)
    assert stale == [], f".env.example lists keys that no code or compose file reads: {stale}"

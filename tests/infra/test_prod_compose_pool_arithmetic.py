"""The connection pool is per process, so its numbers must be chosen together.

`docs/REALTIME_POSITION_COST.md` §3.5 flags this as a **correctness** problem
rather than an optimisation. Postgres' `max_connections` is a server-wide limit,
but SQLAlchemy's pool is created once per worker, so what the api actually asks
for is

    (DB_POOL_SIZE + DB_MAX_OVERFLOW) x API_WORKERS

The base compose file previously hard-coded 10 + 20. At the four workers a
"just add more workers" reflex reaches for, that is 120 against a default limit
of 100 — and the failure does not appear in dev, in tests, or in any single-
worker smoke test. It appears as `too many clients already` on the one day the
platform is busy, which is the worst possible time to learn it.

These are guard tests over the deploy files, in the same spirit as
`tests/test_env_example.py`: nothing here exercises runtime behaviour, and
nothing here would have caught the bug by running the app. They exist so the
arithmetic cannot drift silently, because the three numbers live in three
different places (two `environment:` entries and one command line) and nothing
about editing one of them hints that the others are now wrong.

A note on reading the file. `yaml.safe_load` returns the literal
`"${API_WORKERS:-1}"` — the `${...}` substitution is performed by the compose
CLI, not by YAML, so this module resolves it itself. That is deliberate: it means
the assertion is against the value compose would actually use, rather than
against whatever a helper happened to guess.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_PROD = ROOT / "docker-compose.prod.yml"

# Connections Postgres needs for things that are not the api's pool:
# `alembic upgrade head`, an operator's psql session, the nightly pg_dump and
# its restore drill, and monitoring. Kept as a named constant because it is a
# judgement call, not a derived value — if you disagree with it, disagree here.
RESERVED_CONNECTIONS = 20

_WORKERS_IN_COMMAND = re.compile(r"--workers\s+\$\{API_WORKERS:-(\d+)\}")
_MAX_CONNECTIONS_IN_COMMAND = re.compile(r"max_connections=(\S+)")
# The two shapes a numeric compose value is allowed to take.
_LITERAL = re.compile(r"^\d+$")
_WITH_DEFAULT = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-(\d+)\}$")


def _prod() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))


def _api_env() -> dict[str, str]:
    return _prod()["services"]["api"]["environment"]


def _resolve_int(raw: object, *, where: str) -> int:
    """Resolve a compose numeric value, whether literal or `${VAR:-default}`.

    Anything else fails loudly. A value this cannot read is a value the
    arithmetic below would silently skip — and a guard that skips is the exact
    failure this module was written to prevent.
    """
    text = str(raw).strip()
    if _LITERAL.match(text):
        return int(text)
    match = _WITH_DEFAULT.match(text)
    if match:
        # The default is the number that applies when the operator has not set
        # the variable, i.e. the one an untouched deploy uses.
        return int(match.group(2))
    raise AssertionError(
        f"{where} is {text!r}, which this test cannot read as a number. Use a "
        "plain integer or ${VAR:-<integer>}."
    )


def _db_max_connections() -> int:
    """Pull `max_connections` out of the db service's command line."""
    command = _prod()["services"]["db"]["command"]
    joined = " ".join(str(part) for part in command)
    match = _MAX_CONNECTIONS_IN_COMMAND.search(joined)
    assert match is not None, (
        f"docker-compose.prod.yml no longer sets max_connections explicitly: {joined!r}. "
        "The pool arithmetic below is only checked against a number this repo declares."
    )
    return _resolve_int(match.group(1), where="the db service's max_connections")


def test_the_pool_stays_under_max_connections():
    """The whole point: the api's demand must leave room for everything else."""
    env = _api_env()
    demand = (
        _resolve_int(env["DB_POOL_SIZE"], where="DB_POOL_SIZE")
        + _resolve_int(env["DB_MAX_OVERFLOW"], where="DB_MAX_OVERFLOW")
    ) * _resolve_int(env["API_WORKERS"], where="API_WORKERS")
    limit = _db_max_connections()

    assert demand + RESERVED_CONNECTIONS <= limit, (
        f"the api asks for {demand} connections "
        f"((DB_POOL_SIZE + DB_MAX_OVERFLOW) x API_WORKERS) out of {limit}, leaving "
        f"less than the {RESERVED_CONNECTIONS} reserved for migrations, psql, the "
        "nightly pg_dump and monitoring. Lower DB_POOL_SIZE / DB_MAX_OVERFLOW, "
        "lower API_WORKERS, or raise PG_MAX_CONNECTIONS — but raising it costs "
        "roughly 10 MB of RAM per connection."
    )


def test_the_worker_count_is_declared_and_used_consistently():
    """`--workers` must come from the declared variable, not a bare literal.

    If the command hard-coded `--workers 4` while `API_WORKERS` said 1, the
    arithmetic test above would be checking a number the container never uses —
    a green test guarding the wrong value, which is worse than no test.
    """
    env = _api_env()
    assert "API_WORKERS" in env, (
        "docker-compose.prod.yml no longer declares API_WORKERS in the api's "
        "environment, so the pool arithmetic cannot be checked against the worker "
        "count the container actually runs."
    )

    command = _prod()["services"]["api"]["command"]
    match = _WORKERS_IN_COMMAND.search(command)
    assert match is not None, (
        f"the api command does not take its worker count from ${{API_WORKERS:-N}}: {command!r}"
    )

    declared = _resolve_int(env["API_WORKERS"], where="API_WORKERS")
    in_command = int(match.group(1))
    assert declared == in_command, (
        f"API_WORKERS defaults to {declared} in `environment:` but {in_command} on "
        "the command line. Compose resolves ${VAR:-default} from the host "
        "environment and the .env file, NOT from the service's own `environment:` "
        "block, so the two are separate lookups that only agree by coincidence — "
        "and the pool arithmetic above would be checking the wrong one."
    )


def test_the_code_defaults_are_safe_for_a_single_worker():
    """The base compose file sets no pool variables, so it uses these defaults.

    `docker-compose.yml` alone must therefore also be safe. It runs one worker,
    which is what makes the defaults acceptable — if that ever stops being true,
    this test is the place that says so.
    """
    pool = Settings.model_fields["db_pool_size"].default
    overflow = Settings.model_fields["db_max_overflow"].default

    assert pool + overflow + RESERVED_CONNECTIONS <= _db_max_connections(), (
        f"the Settings defaults ({pool} + {overflow}) leave no room for the "
        f"{RESERVED_CONNECTIONS} reserved connections once the prod stack is up"
    )


def test_the_statement_cache_is_on_when_pooling_is_not_used():
    """asyncpg's prepared-statement cache is an optimisation — and a bug behind
    a transaction-mode pooler.

    It is 100 (asyncpg's own default) here because this overlay talks to
    Postgres directly. The failure mode when it is left on behind PgBouncer is
    `prepared statement "__asyncpg_stmt_N__" does not exist`, which looks like a
    database fault rather than a configuration one. Asserted so that copying
    this file to build the PgBouncer overlay cannot quietly inherit the wrong
    value.
    """
    cache = _resolve_int(_api_env()["DB_STATEMENT_CACHE_SIZE"], where="DB_STATEMENT_CACHE_SIZE")
    assert cache == 100

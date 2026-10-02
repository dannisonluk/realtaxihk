"""The connection pool and prepared-statement settings must reach the driver.

`docs/REALTIME_POSITION_COST.md` §3.5 flags the pool sizing as a **correctness**
problem rather than an optimisation: `pool_size` and `max_overflow` are
per-process, so four workers at the previously hard-coded 10 + 20 ask Postgres
for 120 connections against a default limit of 100. That failure appears only
under production concurrency, which is the worst place to find it.

The test suite could not have caught it, and this file exists because of *why*:
`tests/conftest.py` builds its own engines with `NullPool` against a per-test
database and never calls `app.core.db.get_engine()`. So the application's own
pool configuration was never constructed by any test — it was hard-coded, it was
wrong for the documented deploy, and nothing looked at it.

These tests do not connect. They spy on `create_async_engine` and assert what the
application asked for, which is the part that can be wrong here; whether asyncpg
honours `statement_cache_size` is asyncpg's contract, and is verified separately
by reading `Connection._stmt_cache_enabled` off a live connection.
"""

from __future__ import annotations

import pytest

from app.core import db as db_module
from app.core.config import Settings


@pytest.fixture
def engine_kwargs(monkeypatch):
    """Capture the kwargs `get_engine()` passes to `create_async_engine`.

    `_engine` is reset to `None` so the factory actually runs — it is a
    module-level singleton, and without this the test would read whatever engine
    an earlier test had already built (or build none at all). `monkeypatch`
    restores both the global and the patched factory afterwards.
    """
    captured: dict[str, object] = {}
    real = db_module.create_async_engine

    def spy(url, **kwargs):
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr(db_module, "create_async_engine", spy)
    monkeypatch.setattr(db_module, "_engine", None)
    return captured


def test_pool_sizes_come_from_the_settings(engine_kwargs, monkeypatch):
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: Settings(db_pool_size=3, db_max_overflow=4),
    )

    db_module.get_engine()

    assert engine_kwargs["pool_size"] == 3
    assert engine_kwargs["max_overflow"] == 4


def test_the_statement_cache_size_reaches_asyncpg(engine_kwargs, monkeypatch):
    """The one that makes PgBouncer's transaction pooling safe.

    asyncpg takes this as a **connect** argument, not an engine option, so it has
    to travel inside `connect_args`. Confirmed by mutation that the wrong
    placement does not go unnoticed: passing it as a top-level keyword to
    `create_async_engine` fails the suite, because SQLAlchemy validates engine
    kwargs and raises
    `Invalid argument(s) 'statement_cache_size' sent to create_engine()`.

    The test is still worth having. That error names SQLAlchemy's own argument
    list rather than the deployment problem, so it explains *what* is wrong and
    not *why it matters* — and the assertion here is the one that says the value
    reaches asyncpg at all, which is the part a future refactor of this function
    could quietly drop.
    """
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: Settings(db_statement_cache_size=0),
    )

    db_module.get_engine()

    assert engine_kwargs["connect_args"] == {"statement_cache_size": 0}


def test_the_defaults_are_the_direct_connection_ones():
    """Documented defaults, asserted so a change to them is a visible decision.

    100 is asyncpg's own default and is correct in front of a single Postgres.
    The prod overlay keeps it at 100 deliberately, because that deploy does NOT
    put a pooler in front — `deploy/README.md` documents PgBouncer as an opt-in
    scale path, not part of the default stack. The value has to become 0 only
    once a transaction-mode pooler is actually added, and getting it wrong there
    fails at runtime with `prepared statement "__asyncpg_stmt_N__" does not
    exist`, which reads like a database fault rather than a configuration one.
    """
    settings = Settings()

    assert settings.db_statement_cache_size == 100
    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 20

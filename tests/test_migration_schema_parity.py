"""The schema the migrations build must equal the schema `create_all` builds.

This is the test that was structurally missing. `tests/conftest.py` builds its
template with `Base.metadata.create_all` and never runs a migration, so every
other test in the suite validates the *model* against a schema the models
produced. Nothing compared that schema to the one an actual `alembic upgrade
head` produces — and the two had drifted in ways that mattered:

* `alembic upgrade head` could not initialise a plain Postgres at all (the root
  migration declares `geography(...)` but nothing created the PostGIS
  extension), so the documented deploy only worked because the `postgis/postgis`
  image does it for you. Fixed in `alembic/env.py`.
* 18 of 20 enum columns rendered as a bare `VARCHAR` with no CHECK in *both*
  schemas — the models were the source of that, and it is guarded now by
  `tests/test_enum_check_constraints.py`.

The remaining accepted drift is narrow and asserted explicitly below rather than
just tolerated, so a *new* difference fails loudly instead of blending into a
known-failing baseline. If this test starts failing, the question is not "how do
I update the baseline" but "which schema does production actually have".

These tests need a live Postgres (same requirement as the rest of the suite).
`test_migrations_apply_to_a_plain_postgres` is the only place in the suite that
runs migrations at all.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# The drift that predates this file, captured as data so a *new* entry is a
# failure rather than an accepted-looking line in a diff.
#
# 5 indexes: `users` / `admin_accounts` / `email_verification_tokens` declare
#   `unique=True, index=True` in the models, which SQLAlchemy renders as a
#   separate `ix_*` index; the migrations declared `UniqueConstraint` instead.
# 4 type widths: the models derive VARCHAR length from the longest enum member,
#   the migrations hand-wrote a length. The *type* is VARCHAR in both.
_KNOWN_INDEX_DRIFT = {
    "ix_admin_accounts_email",
    "ix_admin_accounts_username",
    "ix_email_verification_tokens_token_hash",
    "ix_users_email",
    "ix_users_username",
}
_KNOWN_TYPE_DRIFT = {
    ("driver_documents", "kind"),
    ("driver_licence_submissions", "status"),
    ("users", "gender"),
    ("users", "account_status"),
}


def _dsn(database: str) -> str:
    s = get_settings()
    return (
        f"postgresql://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/{database}"
    )


def _async_url(database: str) -> str:
    s = get_settings()
    return (
        f"postgresql+asyncpg://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/{database}"
    )


async def _admin_conn() -> asyncpg.Connection:
    s = get_settings()
    return await asyncpg.connect(
        host=s.postgres_host,
        port=s.postgres_port,
        user=s.postgres_user,
        password=s.postgres_password,
        database="postgres",
    )


@pytest.fixture(scope="module")
def migrated_db() -> str:
    """A scratch database with every migration applied, torn down afterwards.

    Module-scoped because a full `upgrade head` is not cheap and the assertions
    below only read. The name is randomised so two concurrent runs do not drop
    each other's database mid-test — the same reason `conftest.TEMPLATE_DB`
    carries a suffix.
    """
    name = f"realtaxihk_migchk_{uuid.uuid4().hex[:8]}"

    async def create() -> None:
        conn = await _admin_conn()
        try:
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

    async def drop() -> None:
        conn = await _admin_conn()
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(create())
    try:
        # `POSTGRES_DB` is what `Settings.database_url` reads; alembic takes the
        # URL from settings (`alembic/env.py`), not from `-x`.
        previous = os.environ.get("POSTGRES_DB")
        os.environ["POSTGRES_DB"] = name
        try:
            get_settings.cache_clear()
            cfg = Config(str(REPO_ROOT / "alembic.ini"))
            cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
            command.upgrade(cfg, "head")
            yield name
        finally:
            if previous is None:
                os.environ.pop("POSTGRES_DB", None)
            else:
                os.environ["POSTGRES_DB"] = previous
            get_settings.cache_clear()
    finally:
        asyncio.run(drop())


def test_migrations_apply_to_a_plain_postgres(migrated_db: str):
    """A database with no PostGIS preinstalled still reaches head.

    This is the failure that made the whole file necessary: the extension must
    be created before the first migration, and the only deploy path in the docs
    used an image that did it out of band, so nobody noticed it was missing.
    """

    async def check() -> tuple[str, int, int]:
        conn = await asyncpg.connect(dsn=_dsn(migrated_db))
        try:
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            tables = await conn.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
            )
            has_postgis = await conn.fetchval(
                "SELECT count(*) FROM pg_extension WHERE extname = 'postgis'"
            )
            return version, tables, has_postgis
        finally:
            await conn.close()

    version, tables, has_postgis = asyncio.run(check())
    assert version, "alembic_version is empty — the upgrade did not commit"
    assert has_postgis == 1, "postgis was not created by the migration run"
    # 22 app tables (21 + alembic_version is not in public; the exact count is
    # asserted loosely so adding a table does not require touching this test).
    assert tables >= 22, f"expected the full schema, got {tables} tables"


def test_every_metadata_check_constraint_exists_after_migrating(migrated_db: str):
    """The constraint equivalents of the enum guard, checked against real DDL.

    `test_enum_check_constraints.py` proves the models *ask* for the
    constraints; this proves a migrated database actually *has* them. Both are
    needed: the first catches a model that forgot `create_constraint=True`, the
    second catches a migration that never ran or was reverted.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from app.models import Base

    dialect = postgresql.dialect()
    expected: set[tuple[str, str]] = set()
    for table_name, table in Base.metadata.tables.items():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        for line in ddl.splitlines():
            stripped = line.strip().rstrip(",")
            if stripped.startswith("CONSTRAINT ") and " CHECK " in stripped:
                expected.add((table_name, stripped.split()[1]))

    async def actual() -> set[tuple[str, str]]:
        conn = await asyncpg.connect(dsn=_dsn(migrated_db))
        try:
            rows = await conn.fetch(
                """
                SELECT cl.relname, c.conname
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE c.contype = 'c'
                  AND n.nspname = 'public'
                  AND cl.relname <> 'spatial_ref_sys'
                """
            )
            return {(r["relname"], r["conname"]) for r in rows}
        finally:
            await conn.close()

    have = asyncio.run(actual())
    assert expected - have == set(), f"missing from migrated schema: {expected - have}"
    assert have - expected == set(), f"not in models: {have - expected}"


def test_the_remaining_drift_is_only_the_documented_drift(migrated_db: str):
    """`alembic check` must report the known baseline and nothing new.

    Kept as a test rather than a note, because "alembic check fails, that is
    expected" is exactly the kind of standing excuse under which a real
    difference hides.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models import Base

    engine = create_async_engine(_async_url(migrated_db), poolclass=NullPool)

    async def diff_against_live_schema():
        async with engine.connect() as conn:

            def run(sync_conn):
                ctx = MigrationContext.configure(
                    sync_conn,
                    opts={"compare_type": True, "include_object": _include_object},
                )
                return compare_metadata(ctx, Base.metadata)

            return await conn.run_sync(run)

    try:
        diff = asyncio.run(diff_against_live_schema())
    finally:
        asyncio.run(engine.dispose())

    added_indexes: set[str] = set()
    modified: set[tuple[str, str]] = set()
    unexpected: list[str] = []
    for entry in diff:
        # `compare_metadata` mixes bare tuples (indexes) with single-element
        # lists (`modify_type`), which is why both shapes are unwrapped here.
        op = entry[0] if isinstance(entry, tuple) else entry[0][0]
        payload = entry if isinstance(entry, tuple) else entry[0]
        if op in ("add_index", "remove_index"):
            added_indexes.add(
                f"REMOVE:{payload[1].name}" if op == "remove_index" else payload[1].name
            )
        elif op == "modify_type":
            modified.add((payload[2], payload[3]))
        else:
            unexpected.append(str(entry)[:200])

    assert unexpected == [], f"new drift operations appeared: {unexpected}"
    # Removing one of the known-duplicated indexes is a real change, not drift.
    assert not {i for i in added_indexes if i.startswith("REMOVE:")}, added_indexes
    assert added_indexes <= _KNOWN_INDEX_DRIFT, (
        f"new index drift: {added_indexes - _KNOWN_INDEX_DRIFT}"
    )
    assert modified <= _KNOWN_TYPE_DRIFT, f"unexpected type drift: {modified - _KNOWN_TYPE_DRIFT}"


def _include_object(obj, name, type_, reflected, compare_to):
    """Mirror `alembic/env.py` so the comparison sees the same object set."""
    return not (type_ in ("table", "index") and reflected and compare_to is None)

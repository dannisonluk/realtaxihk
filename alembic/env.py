import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Real connection URL comes from app settings (POSTGRES_* in .env), never hard-coded.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


# Object kinds autogenerate must never DROP. Filtering only `table` misses the
# indexes the extensions create *on their own tables* (tiger's spatial indexes,
# for one) — a reflected index with no counterpart in our metadata is read as
# "drop me", and `compare_type=True` below is what makes that reachable. This
# project already carries accepted drift, so the signal that matters is "did a
# NEW entry appear"; a wider filter keeps that signal clean.
_IGNORED_TYPES = ("table", "index")


def include_object(obj, name, type_, reflected, compare_to):
    """Never let autogenerate touch objects it doesn't know from our metadata.

    PostGIS/tiger/topology extensions register their own tables *and* indexes;
    without this filter alembic emits DROPs for them (fatal on any postgis
    database).
    """
    return not (type_ in _IGNORED_TYPES and reflected and compare_to is None)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to
    the script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        # PostGIS must exist before the FIRST migration, not after.
        # `9307e944a592` declares `geography(POINT, 4326)` columns on
        # `driver_profiles` and `orders`, and Postgres resolves that type name at
        # CREATE TABLE time — so `alembic upgrade head` against a database
        # without the extension dies with `type "geography" does not exist`.
        #
        # Nothing caught this because the only documented deploy is the
        # `postgis/postgis` image, whose entrypoint creates the extension for
        # you, and the test suite skips migrations entirely (it builds the schema
        # with `Base.metadata.create_all` after its own `CREATE EXTENSION`, see
        # tests/conftest.py). The failure is specific to a managed/plain
        # Postgres — the one path nobody had run.
        #
        # Inside `begin_transaction()` on purpose: emitting DDL on the connection
        # *before* this block leaves Postgres' transactional DDL in a state where
        # the migration block commits nothing, and every version reports as
        # applied against an empty schema.
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

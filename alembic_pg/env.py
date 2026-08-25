"""Alembic environment for the POSTGRES migration chain.

Parallel to alembic/ (the MariaDB chain) for the transition window — see
docs/plans/2026-Q3/2026-08-21-alembic-pg-baseline-design.md and ADR-0009.
Runs on the async asyncpg driver (this repo installs no sync Postgres
driver), so migrations execute through run_sync.
"""

import asyncio
import logging
import os

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

# app.main pulls in every model module (the models package __init__ misses
# some), populating SQLModel.metadata for autogenerate.
import app.main  # noqa: F401, E402
from alembic import context
from app.config import settings

config = context.config

# URL precedence (highest to lowest), matching alembic/env.py:
#   1. -x dbUrl=...     CLI override
#   2. $ALEMBIC_DB_URL  programmatic override (tests/conftest.py)
#   3. settings.DATABASE_URL  the application's configured DB (async URL —
#      this chain runs on asyncpg, so the async URL is the right one)
db_url = (
    context.get_x_argument(as_dictionary=True).get("dbUrl")
    or os.getenv("ALEMBIC_DB_URL")
    or settings.DATABASE_URL
)
config.set_main_option("sqlalchemy.url", db_url)

logging.basicConfig(
    format="%(levelname)-5.5s [%(name)s] %(message)s",
    level=logging.INFO,
)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database via the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

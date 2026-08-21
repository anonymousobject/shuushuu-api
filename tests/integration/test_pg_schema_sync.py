"""Models vs the POSTGRES migration chain (alembic_pg/).

The dual-window enforcement from the alembic-pg-baseline design doc: one
database is built from the models (build_pg_schema — create_all, citext,
CHECKs, triggers) and one from `alembic_pg upgrade head`; their catalogs must
be identical. A model change without a matching migration in alembic_pg/
turns this red, exactly as tests/integration/test_schema_sync.py does for the
MariaDB chain.

Run with: pytest tests/integration/test_pg_schema_sync.py --schema-sync -v
"""

import os
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.pg_schema import build_pg_schema
from tests.conftest import TEST_DATABASE_URL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.schema_sync,
    pytest.mark.postgres_only,
    # Fixed-name databases: keep on one xdist worker (--dist loadgroup).
    pytest.mark.xdist_group("pg_schema_sync"),
]

_DB_MODELS = "shuushuu_schema_models_pg"
_DB_MIGRATIONS = "shuushuu_schema_migrations_pg"

_SNAPSHOT_QUERIES = {
    "columns": """
        SELECT table_name || '.' || column_name || ' ' || udt_name
               || ' nullable=' || is_nullable
               || ' default=' || COALESCE(column_default, '-')
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """,
    "indexes": "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'",
    "constraints": """
        SELECT conrelid::regclass || ' ' || conname || ' '
               || pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
    """,
    "triggers": """
        SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal
    """,
}


async def _snapshot(database: str) -> dict[str, set[str]]:
    engine = create_async_engine(make_url(TEST_DATABASE_URL).set(database=database))
    try:
        async with engine.connect() as conn:
            return {
                name: {row[0] for row in await conn.execute(text(query))}
                for name, query in _SNAPSHOT_QUERIES.items()
            }
    finally:
        await engine.dispose()


async def _fresh_database(database: str) -> None:
    admin = create_async_engine(
        make_url(TEST_DATABASE_URL).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            await conn.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        await admin.dispose()


async def test_pg_models_match_migration_chain():
    """create_all-from-models and alembic_pg must produce identical schemas."""
    await _fresh_database(_DB_MODELS)
    await _fresh_database(_DB_MIGRATIONS)

    models_engine = create_async_engine(make_url(TEST_DATABASE_URL).set(database=_DB_MODELS))
    try:
        async with models_engine.begin() as conn:
            await build_pg_schema(conn)
    finally:
        await models_engine.dispose()

    # Subprocess, not programmatic: the async env.py runs its own event loop,
    # which cannot nest inside this test's.
    migrations_url = make_url(TEST_DATABASE_URL).set(database=_DB_MIGRATIONS)
    env = os.environ.copy()
    env["ALEMBIC_DB_URL"] = migrations_url.render_as_string(hide_password=False)
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", "alembic.pg.ini", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"alembic_pg upgrade failed:\n{result.stderr}"

    models = await _snapshot(_DB_MODELS)
    migrations = await _snapshot(_DB_MIGRATIONS)

    for section in _SNAPSHOT_QUERIES:
        only_models = models[section] - migrations[section]
        only_migrations = migrations[section] - models[section]
        # alembic_version exists only on the chain side, by design.
        only_migrations = {s for s in only_migrations if "alembic_version" not in s}
        assert not only_models and not only_migrations, (
            f"{section} differ.\n"
            f"Only in models-built schema:\n  " + "\n  ".join(sorted(only_models)) + "\n"
            "Only in chain-built schema:\n  " + "\n  ".join(sorted(only_migrations))
        )

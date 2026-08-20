"""Postgres schema bootstrap for the pre-baseline transition.

Builds the full application schema on a Postgres database from the SQLModel
metadata — the stand-in for a Postgres Alembic baseline until one exists (see
docs/plans/2026-Q3/2026-08-20-tests-on-postgres-design.md). Shared by
scripts/postgres_poc.py and tests/conftest.py so the POC database and the
test databases cannot drift apart.
"""

from collections import defaultdict

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import quoted_name

# citext has no length modifier, so the VARCHAR(n) caps these columns have on
# MariaDB move to CHECK constraints (Postgres-only: a CHECK in the models'
# __table_args__ would change the MariaDB DDL and break schema-sync). ADR-0008.
_LENGTH_CHECKS = (
    "ALTER TABLE users ADD CONSTRAINT ck_users_username_len CHECK (char_length(username) <= 30)",
    "ALTER TABLE users ADD CONSTRAINT ck_users_email_len CHECK (char_length(email) <= 120)",
    "ALTER TABLE tags ADD CONSTRAINT ck_tags_title_len CHECK (char_length(title) <= 255)",
)


def dedupe_index_names(metadata: MetaData) -> None:
    """Rename index names that repeat across tables. Idempotent.

    MySQL scopes index names per table; Postgres per schema. The legacy schema
    reuses a few names (idx_date, idx_tag_id), which is fine on MariaDB but a
    DuplicateTableError on Postgres. Transition-only shim: a real Postgres
    baseline migration would normalize the names instead.
    """
    by_name = defaultdict(list)
    for table in metadata.tables.values():
        for index in table.indexes:
            by_name[index.name].append((table, index))
    for entries in by_name.values():
        if len(entries) > 1:
            for table, index in entries:
                index.name = quoted_name(f"{table.name}_{index.name}", None)


async def build_pg_schema(conn: AsyncConnection) -> None:
    """Drop and rebuild the public schema from the models. Destroys all data.

    DROP SCHEMA CASCADE instead of drop_all: the FK graph has cycles that
    Postgres won't untangle without CASCADE (MySQL drop_all just disables FK
    checks). citext must be recreated after the drop — it lives in public.
    """
    # app.main, not app.models: the models package __init__ does not import
    # every model module (e.g. user_suspension), but the app wiring does.
    from sqlmodel import SQLModel

    import app.main  # noqa: F401  (registers all tables on SQLModel.metadata)

    dedupe_index_names(SQLModel.metadata)
    await conn.execute(text("DROP SCHEMA public CASCADE"))
    await conn.execute(text("CREATE SCHEMA public"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    await conn.run_sync(SQLModel.metadata.create_all)
    for ddl in _LENGTH_CHECKS:
        await conn.execute(text(ddl))

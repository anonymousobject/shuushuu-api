"""Postgres schema bootstrap from the models.

The models' view of the schema, which tests/integration/test_schema_sync.py
compares against the migration chain. Everything the chain creates that
create_all cannot express lives here: the citext extension and the counter
triggers (ADR-0009).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.pg_triggers import install_counter_triggers


async def build_pg_schema(conn: AsyncConnection) -> None:
    """Drop and rebuild the public schema from the models. Destroys all data.

    DROP SCHEMA CASCADE instead of drop_all: the FK graph has cycles that
    drop_all cannot untangle. citext must be recreated after the drop — it
    lives in public.
    """
    # app.main, not app.models: the models package __init__ does not import
    # every model module (e.g. user_suspension), but the app wiring does.
    from sqlmodel import SQLModel

    import app.main  # noqa: F401  (registers all tables on SQLModel.metadata)

    await conn.execute(text("DROP SCHEMA public CASCADE"))
    await conn.execute(text("CREATE SCHEMA public"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    await conn.run_sync(SQLModel.metadata.create_all)
    await install_counter_triggers(conn)

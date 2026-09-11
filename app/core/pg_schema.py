"""Postgres schema bootstrap from the models.

The models' view of the schema, which tests/integration/test_schema_sync.py
compares against the migration chain. Everything the chain creates that
create_all cannot express lives here: the citext extension, the counter
triggers (ADR-0009), and the tag-search fold function and indexes (mirrors
alembic/versions/0004_tag_search_fold.py).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.pg_triggers import install_counter_triggers

_SEARCH_EXTENSIONS = ("pg_trgm", "unaccent", "fuzzystrmatch")

# Kept in sync by hand with alembic/versions/0004_tag_search_fold.py: the
# migration runs these via CREATE INDEX CONCURRENTLY in an autocommit block,
# which this function's single transaction cannot do, so the two copies
# differ only in that one word.
_FOLD_FUNCTION = """
CREATE OR REPLACE FUNCTION public.fold_search_text(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$
"""

_SEARCH_INDEXES = (
    (
        "ix_tags_title_fold_trgm",
        "tags",
        "USING gin (public.fold_search_text(title::text) gin_trgm_ops)",
    ),
    ("ix_tags_desc_fold_trgm", "tags", 'USING gin (public.fold_search_text("desc") gin_trgm_ops)'),
    (
        "ix_tag_external_links_url_fold_trgm",
        "tag_external_links",
        "USING gin (public.fold_search_text(url) gin_trgm_ops)",
    ),
    (
        "ix_tags_title_fold_prefix",
        "tags",
        "(public.fold_search_text(title::text) text_pattern_ops)",
    ),
)


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
    for extension in _SEARCH_EXTENSIONS:
        await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
    await conn.run_sync(SQLModel.metadata.create_all)
    await install_counter_triggers(conn)
    await conn.execute(text(_FOLD_FUNCTION))
    for name, table, definition in _SEARCH_INDEXES:
        await conn.execute(text(f"CREATE INDEX {name} ON {table} {definition}"))

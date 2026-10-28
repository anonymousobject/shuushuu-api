"""tag search: fold function, trigram and prefix indexes

Revision ID: 0004_tag_search_fold
Revises: e20bac5f3ac3
Create Date: 2026-09-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_tag_search_fold"
down_revision: str | Sequence[str] | None = "e20bac5f3ac3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "unaccent", "fuzzystrmatch")

# IMMUTABLE is a promise that lets the function sit inside an index
# expression; changing the unaccent dictionary later means reindexing.
# Both names are schema-qualified so the migration and the app inline the
# same function whatever search_path says.
FOLD_FUNCTION = """
CREATE OR REPLACE FUNCTION public.fold_search_text(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$
"""

# (name, table, index definition after "ON <table>")
INDEXES = (
    ("ix_tags_title_fold_trgm", "tags", "USING gin (public.fold_search_text(title::text) gin_trgm_ops)"),
    ("ix_tags_desc_fold_trgm", "tags", 'USING gin (public.fold_search_text("desc") gin_trgm_ops)'),
    (
        "ix_tag_external_links_url_fold_trgm",
        "tag_external_links",
        "USING gin (public.fold_search_text(url) gin_trgm_ops)",
    ),
    ("ix_tags_title_fold_prefix", "tags", "(public.fold_search_text(title::text) text_pattern_ops)"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for extension in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    op.execute(FOLD_FUNCTION)
    # CONCURRENTLY cannot run inside a transaction; the autocommit block
    # commits the migration so far and runs these statements on their own.
    with op.get_context().autocommit_block():
        for name, table, definition in INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {definition}")


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for name, _table, _definition in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute("DROP FUNCTION IF EXISTS public.fold_search_text(text)")
    # The extensions stay: dropping them is an operator decision.

"""image sort seek indexes

Revision ID: 0009_image_sort_seek_indexes
Revises: 0008_markup_version
Create Date: 2026-11-09 12:00:00.000000

The search-context neighbours lookup (app/services/image_neighbors.py) seeks
within one sort value in image_id order. For last_post that value is usually
NULL (most images have no comments), and the single-column idx_last_post
returns those rows in no useful order, so the lookup sorted every one of them.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_image_sort_seek_indexes"
down_revision: str | Sequence[str] | None = "0008_markup_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, index definition after "ON images")
INDEXES = (("idx_last_post_image_id", "(last_post, image_id)"),)


def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY keeps images writable while the index builds; it cannot run
    # inside a transaction, so the autocommit block runs it on its own.
    with op.get_context().autocommit_block():
        for name, definition in INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON images {definition}")


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for name, _definition in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

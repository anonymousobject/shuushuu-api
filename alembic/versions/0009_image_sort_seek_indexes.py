"""image sort seek indexes

Revision ID: 0009_image_sort_seek_indexes
Revises: 0008_markup_version
Create Date: 2026-11-09 12:00:00.000000

The search-context neighbours lookup (app/services/image_neighbors.py) seeks
within one sort value in image_id order. For last_post that value is usually
NULL (most images have no comments), and the single-column idx_last_post
returns those rows in no useful order, so the lookup sorted every one of them.

bayesian_rating and favorites have large tie groups (most images are rated 0.0),
and the lookup's first row past a group must come in image_id order: ascending
or descending depending on the sort direction and side. A btree scans either
way, so (col, image_id) serves (col, id) both ascending or both descending,
and (col, image_id DESC) serves the two mixed orders.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0009_image_sort_seek_indexes"
down_revision: str | Sequence[str] | None = "0008_markup_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, index definition after "ON images")
INDEXES = (
    ("idx_last_post_image_id", "(last_post, image_id)"),
    ("idx_bayesian_rating_image_id", "(bayesian_rating, image_id)"),
    ("idx_bayesian_rating_image_id_desc", "(bayesian_rating, image_id DESC)"),
    ("idx_favorites_image_id", "(favorites, image_id)"),
    ("idx_favorites_image_id_desc", "(favorites, image_id DESC)"),
)


def _is_valid(name: str) -> bool | None:
    """pg_index.indisvalid for the index `name` on images; None when there is none."""
    valid: bool | None = op.get_bind().scalar(
        sa.text(
            "SELECT ix.indisvalid FROM pg_index ix JOIN pg_class c ON c.oid = ix.indexrelid"
            " WHERE ix.indrelid = 'images'::regclass AND c.relname = :name"
        ),
        {"name": name},
    )
    return valid


def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY keeps images writable while the index builds; it cannot run
    # inside a transaction, so the autocommit block runs it on its own.
    # `alembic upgrade --sql` has no database to inspect, so it emits the builds alone.
    inspect = not context.is_offline_mode()
    with op.get_context().autocommit_block():
        for name, definition in INDEXES:
            # A failed or cancelled concurrent build leaves an INVALID index under
            # its name, which IF NOT EXISTS would skip for good: drop it first.
            if inspect and _is_valid(name) is False:
                op.execute(f"DROP INDEX CONCURRENTLY {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON images {definition}")
        # IF NOT EXISTS also skips a name some other index already holds. Fail
        # rather than record this revision without every index valid.
        not_valid = [name for name, _definition in INDEXES if inspect and not _is_valid(name)]
        if not_valid:
            raise RuntimeError(
                "0009: these indexes on images are missing or INVALID after the build: "
                f"{', '.join(not_valid)}. Find what holds the name (pg_class), drop it, "
                "and run the migration again."
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for name, _definition in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

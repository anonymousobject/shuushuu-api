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

Each composite also serves everything the single-column index on its first
column did, so those three are dropped: images is updated on every comment,
favourite and rating, and each index is one more entry to write. Dropping
idx_last_post is a gain of its own. On a 1.1M-image database the planner chose
it over the composite for a ``last_post DESC`` listing and sorted the whole
NULL group (342 ms, against 0.1 ms without it); no other measured listing,
count or seek got slower.
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
# (name, index definition after "ON images"): each a prefix of a composite above.
REPLACED_INDEXES = (
    ("idx_last_post", "(last_post)"),
    ("idx_bayesian_rating", "(bayesian_rating)"),
    ("idx_favorites", "(favorites)"),
)


def _index(name: str) -> sa.Row[tuple[bool, str]] | None:
    """(indisvalid, pg_get_indexdef) for the index `name` on images; None when there is none."""
    return (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT ix.indisvalid, pg_get_indexdef(ix.indexrelid)"
                " FROM pg_index ix JOIN pg_class c ON c.oid = ix.indexrelid"
                " WHERE ix.indrelid = 'images'::regclass AND c.relname = :name"
            ),
            {"name": name},
        )
        .one_or_none()
    )


def _is_built(name: str, definition: str) -> bool:
    """Whether `name` is a valid index on images with exactly `definition`."""
    index = _index(name)
    if index is None:
        return False
    valid, indexdef = index
    # The whole tail, so a partial index's WHERE or an INCLUDE does not pass.
    return bool(valid and indexdef.endswith(f" USING btree {definition}"))


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
            if inspect and (index := _index(name)) is not None and not index[0]:
                op.execute(f"DROP INDEX CONCURRENTLY {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON images {definition}")
        # IF NOT EXISTS also skips a name some other index already holds, whatever
        # that index is. Fail rather than record this revision without every index
        # valid and as defined here.
        not_built = [
            name for name, definition in INDEXES if inspect and not _is_built(name, definition)
        ]
        if not_built:
            raise RuntimeError(
                "0009: these indexes on images are missing, INVALID or defined differently "
                f"after the build: {', '.join(not_built)}. Find what holds the name "
                "(pg_class, pg_get_indexdef), drop it, and run the migration again."
            )
        # Only now that every replacement is in place.
        for name, _definition in REPLACED_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        # The single-column indexes first, so no query is left without either.
        for name, definition in REPLACED_INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON images {definition}")
        for name, _definition in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

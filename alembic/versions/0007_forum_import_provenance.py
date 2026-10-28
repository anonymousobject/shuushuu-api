"""forum_import_provenance

Revision ID: 0007_forum_import_provenance
Revises: 0006_add_forum_tables
Create Date: 2026-07-07 17:25:55.402118

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_forum_import_provenance"
down_revision: str | Sequence[str] | None = "0006_add_forum_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "forum_categories",
        sa.Column("legacy_forum_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "forum_categories_legacy_forum_id_key", "forum_categories", ["legacy_forum_id"]
    )
    op.add_column(
        "forum_threads",
        sa.Column("legacy_topic_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "forum_threads_legacy_topic_id_key", "forum_threads", ["legacy_topic_id"]
    )
    op.add_column("forum_posts", sa.Column("legacy_post_id", sa.Integer(), nullable=True))
    op.add_column("forum_posts", sa.Column("legacy_poster_id", sa.Integer(), nullable=True))
    op.add_column("forum_posts", sa.Column("legacy_username", sa.String(255), nullable=True))
    op.create_unique_constraint("forum_posts_legacy_post_id_key", "forum_posts", ["legacy_post_id"])
    op.create_index("ix_forum_posts_legacy_poster_id", "forum_posts", ["legacy_poster_id"])

    # NOTE: the "Archived User" system account is NOT seeded here. It is import
    # infrastructure, created on demand at import run time by
    # app.core.archived_user.ensure_archived_user(). Seeding it in the migration
    # would place a row in every database (including freshly-rebuilt test DBs),
    # colliding with the test harness's fixed-id fixture users; deferring it to
    # run time keeps databases that never run an import untouched.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_forum_posts_legacy_poster_id", table_name="forum_posts")
    op.drop_constraint("forum_posts_legacy_post_id_key", "forum_posts", type_="unique")
    op.drop_column("forum_posts", "legacy_username")
    op.drop_column("forum_posts", "legacy_poster_id")
    op.drop_column("forum_posts", "legacy_post_id")
    op.drop_constraint("forum_threads_legacy_topic_id_key", "forum_threads", type_="unique")
    op.drop_column("forum_threads", "legacy_topic_id")
    op.drop_constraint("forum_categories_legacy_forum_id_key", "forum_categories", type_="unique")
    op.drop_column("forum_categories", "legacy_forum_id")

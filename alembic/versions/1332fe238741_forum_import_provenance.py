"""forum_import_provenance

Revision ID: 1332fe238741
Revises: f565e631d8c2
Create Date: 2026-07-07 17:25:55.402118

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '1332fe238741'
down_revision: str | Sequence[str] | None = 'f565e631d8c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "forum_categories",
        sa.Column("legacy_forum_id", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.create_index(
        "uq_forum_categories_legacy_forum_id", "forum_categories", ["legacy_forum_id"], unique=True
    )
    op.add_column(
        "forum_threads",
        sa.Column("legacy_topic_id", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.create_index(
        "uq_forum_threads_legacy_topic_id", "forum_threads", ["legacy_topic_id"], unique=True
    )
    op.add_column(
        "forum_posts", sa.Column("legacy_post_id", mysql.INTEGER(unsigned=True), nullable=True)
    )
    op.add_column(
        "forum_posts", sa.Column("legacy_poster_id", mysql.INTEGER(unsigned=True), nullable=True)
    )
    op.add_column("forum_posts", sa.Column("legacy_username", sa.String(255), nullable=True))
    op.create_index(
        "uq_forum_posts_legacy_post_id", "forum_posts", ["legacy_post_id"], unique=True
    )
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
    op.drop_index("uq_forum_posts_legacy_post_id", table_name="forum_posts")
    op.drop_column("forum_posts", "legacy_username")
    op.drop_column("forum_posts", "legacy_poster_id")
    op.drop_column("forum_posts", "legacy_post_id")
    op.drop_index("uq_forum_threads_legacy_topic_id", table_name="forum_threads")
    op.drop_column("forum_threads", "legacy_topic_id")
    op.drop_index("uq_forum_categories_legacy_forum_id", table_name="forum_categories")
    op.drop_column("forum_categories", "legacy_forum_id")

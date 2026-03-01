"""convert all tables from utf8mb3 to utf8mb4

Revision ID: 92f9d7890c30
Revises: cab3f028c1e2
Create Date: 2026-03-01 14:19:55.845256

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '92f9d7890c30'
down_revision: str | Sequence[str] | None = 'cab3f028c1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All tables in the database, ordered smallest-first so large tables
# (tag_links, favorites, images) are converted last.
TABLES = [
    "admin_actions",
    "alembic_version",
    "banners",
    "character_source_links",
    "comment_reports",
    "donations",
    "eva_theme",
    "groups",
    "group_perms",
    "image_ratings_avg",
    "image_report_tag_suggestions",
    "image_reviews",
    "image_status_history",
    "news",
    "perms",
    "tips",
    "user_banner_pins",
    "user_banner_preferences",
    "user_groups",
    "user_perms",
    "user_suspensions",
    "quicklinks",
    "refresh_tokens",
    "tag_audit_log",
    "tag_external_links",
    "review_votes",
    "tw_tagcluster",
    "tw_tags",
    "tw_closest",
    "privmsgs",
    "posts",
    "tags",
    "tag_history",
    "users",
    "image_ratings",
    "image_reports",
    "tw_taglink",
    "favorites",
    "images",
    "tag_links",
]


def upgrade() -> None:
    """Convert all tables from utf8mb3 to utf8mb4 to support full Unicode (emoji etc)."""
    # Convert the database default charset first
    op.execute("ALTER DATABASE CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

    for table in TABLES:
        op.execute(
            f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )


def downgrade() -> None:
    """Revert all tables from utf8mb4 back to utf8mb3."""
    for table in reversed(TABLES):
        op.execute(
            f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci"
        )

    op.execute("ALTER DATABASE CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci")

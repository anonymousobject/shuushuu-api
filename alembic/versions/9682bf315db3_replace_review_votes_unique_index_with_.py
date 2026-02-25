"""replace review_votes unique index with non-unique

The legacy unique index on (image_id, user_id) prevents users from voting on
multiple reviews for the same image. Replace it with a non-unique index to
allow separate votes per review session while keeping query performance.

Revision ID: 9682bf315db3
Revises: c8dc7007b860
Create Date: 2026-02-21 16:43:13.988996

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9682bf315db3'
down_revision: str | Sequence[str] | None = 'c8dc7007b860'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop unique index on (image_id, user_id), replace with non-unique.

    MariaDB requires an index on image_id to support the foreign key constraint,
    so we create the non-unique replacement first, then drop the unique one.
    """
    op.create_index(
        "idx_review_votes_image_user_new",
        "review_votes",
        ["image_id", "user_id"],
        unique=False,
    )
    op.drop_index("idx_review_votes_image_user", table_name="review_votes")
    # Rename to final name
    op.execute(
        "ALTER TABLE review_votes RENAME INDEX idx_review_votes_image_user_new"
        " TO idx_review_votes_image_user"
    )


def downgrade() -> None:
    """Restore unique index on (image_id, user_id)."""
    op.create_index(
        "idx_review_votes_image_user_new",
        "review_votes",
        ["image_id", "user_id"],
        unique=True,
    )
    op.drop_index("idx_review_votes_image_user", table_name="review_votes")
    op.execute(
        "ALTER TABLE review_votes RENAME INDEX idx_review_votes_image_user_new"
        " TO idx_review_votes_image_user"
    )

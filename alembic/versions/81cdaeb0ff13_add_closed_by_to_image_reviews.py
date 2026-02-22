"""add closed_by to image_reviews

Tracks which admin closed a review early. NULL means automatic (deadline job).
Backfills from admin_actions where action_type=5 (REVIEW_CLOSE) and user_id IS NOT NULL.

Revision ID: 81cdaeb0ff13
Revises: 9682bf315db3
Create Date: 2026-02-21 17:51:42.422076

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import INTEGER


# revision identifiers, used by Alembic.
revision: str = '81cdaeb0ff13'
down_revision: str | Sequence[str] | None = '9682bf315db3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add closed_by column, FK, index, and backfill from admin_actions."""
    # Add column
    op.add_column(
        "image_reviews",
        sa.Column("closed_by", INTEGER(unsigned=True), nullable=True),
    )

    # Add FK constraint
    op.create_foreign_key(
        "fk_image_reviews_closed_by",
        "image_reviews",
        "users",
        ["closed_by"],
        ["user_id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    # Add index
    op.create_index(
        "fk_image_reviews_closed_by",
        "image_reviews",
        ["closed_by"],
    )

    # Backfill from admin_actions: action_type=5 is REVIEW_CLOSE
    op.execute(
        """
        UPDATE image_reviews ir
        INNER JOIN admin_actions aa
            ON aa.review_id = ir.review_id
            AND aa.action_type = 5
            AND aa.user_id IS NOT NULL
        SET ir.closed_by = aa.user_id
        """
    )


def downgrade() -> None:
    """Remove closed_by column."""
    op.drop_constraint("fk_image_reviews_closed_by", "image_reviews", type_="foreignkey")
    op.drop_index("fk_image_reviews_closed_by", table_name="image_reviews")
    op.drop_column("image_reviews", "closed_by")

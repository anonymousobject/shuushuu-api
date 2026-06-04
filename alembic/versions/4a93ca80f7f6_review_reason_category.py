"""review reason_category

Revision ID: 4a93ca80f7f6
Revises: ba007b19d0f1
Create Date: 2026-06-03 19:51:49.359506

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4a93ca80f7f6'
down_revision: str | Sequence[str] | None = 'ba007b19d0f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename image_reviews.review_type -> reason_category.

    Values are unchanged: the only prior value 1 (Appropriateness) now reads as
    DeactivationReason.INAPPROPRIATE (1) — compatible meaning, no data conversion.
    """
    op.execute(
        "ALTER TABLE image_reviews "
        "CHANGE COLUMN review_type reason_category INT NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    """Rename reason_category back to review_type."""
    op.execute(
        "ALTER TABLE image_reviews "
        "CHANGE COLUMN reason_category review_type INT NOT NULL DEFAULT 1"
    )

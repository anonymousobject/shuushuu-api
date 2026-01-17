"""add suggestion_type column

Revision ID: d1f00dc589f0
Revises: 9efa03a1b318
Create Date: 2026-01-17 15:14:07.996314

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f00dc589f0"
down_revision: str | Sequence[str] | None = "9efa03a1b318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add suggestion_type column to image_report_tag_suggestions."""
    op.add_column(
        "image_report_tag_suggestions",
        sa.Column(
            "suggestion_type",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="1",
        ),
    )
    # Add index for filtering by type
    op.create_index(
        "idx_suggestion_type",
        "image_report_tag_suggestions",
        ["suggestion_type"],
        unique=False,
    )


def downgrade() -> None:
    """Remove suggestion_type column."""
    op.drop_index("idx_suggestion_type", table_name="image_report_tag_suggestions")
    op.drop_column("image_report_tag_suggestions", "suggestion_type")

"""add r2_location to images

Revision ID: 6ad847e3bc00
Revises: 8c950e7fa6f2
Create Date: 2026-04-17 21:27:43.692812

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ad847e3bc00"
down_revision: str | Sequence[str] | None = "8c950e7fa6f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add r2_location column to images.

    0 = NONE (not in R2 yet), 1 = PUBLIC bucket, 2 = PRIVATE bucket.
    Default NONE — backfill is a separate one-shot run.
    """
    op.add_column(
        "images",
        sa.Column(
            "r2_location",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="0",
        ),
    )
    # Index supports reconcile/health queries (WHERE r2_location = 0)
    op.create_index(
        "idx_r2_location",
        "images",
        ["r2_location"],
        unique=False,
    )


def downgrade() -> None:
    """Remove r2_location column."""
    op.drop_index("idx_r2_location", table_name="images")
    op.drop_column("images", "r2_location")

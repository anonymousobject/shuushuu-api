"""Drop avatar_type column from users table

This column is deprecated and unused. Avatar storage now uses MD5 hash
filenames directly without needing a separate type indicator.

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2025-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop deprecated avatar_type column."""
    op.drop_column("users", "avatar_type")


def downgrade() -> None:
    """Recreate avatar_type column."""
    op.add_column(
        "users",
        sa.Column("avatar_type", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

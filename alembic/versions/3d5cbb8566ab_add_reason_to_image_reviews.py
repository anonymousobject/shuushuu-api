"""add reason to image_reviews

Revision ID: 3d5cbb8566ab
Revises: 81cdaeb0ff13
Create Date: 2026-02-22 19:04:17.634719

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d5cbb8566ab'
down_revision: str | Sequence[str] | None = '81cdaeb0ff13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "image_reviews",
        sa.Column("reason", sa.String(1000), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("image_reviews", "reason")

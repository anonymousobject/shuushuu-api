"""add unique constraint on banners full_image

Revision ID: 47b415e78d5c
Revises: 3d5cbb8566ab
Create Date: 2026-02-26 14:27:50.650937

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '47b415e78d5c'
down_revision: str | Sequence[str] | None = '3d5cbb8566ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq_banners_full_image", "banners", ["full_image"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_banners_full_image", "banners", type_="unique")

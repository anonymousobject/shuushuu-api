"""increase tags_title length to 255

Revision ID: c25a53d7e1e6
Revises: 6ad847e3bc00
Create Date: 2026-04-23 20:21:33.478986

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c25a53d7e1e6'
down_revision: str | Sequence[str] | None = '6ad847e3bc00'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Increase tags.title from VARCHAR(150) to VARCHAR(255)."""
    op.alter_column(
        'tags',
        'title',
        existing_type=sa.String(length=150),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Revert tags.title to VARCHAR(150).

    Note: this will fail if any existing rows have title > 150 chars.
    """
    op.alter_column(
        'tags',
        'title',
        existing_type=sa.String(length=255),
        type_=sa.String(length=150),
        existing_nullable=True,
    )

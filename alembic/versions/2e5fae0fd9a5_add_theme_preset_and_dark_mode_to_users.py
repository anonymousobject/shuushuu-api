"""add theme_preset and dark_mode to users

Revision ID: 2e5fae0fd9a5
Revises: 7b2101b37080
Create Date: 2026-05-23 15:56:42.813856

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e5fae0fd9a5'
down_revision: str | Sequence[str] | None = '7b2101b37080'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add theme_preset and dark_mode preference columns to users table.

    Both columns are nullable with no default — NULL means "user hasn't picked",
    so the frontend's localStorage value (or system default for dark mode) wins.
    """
    op.add_column('users', sa.Column('theme_preset', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('dark_mode', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove theme_preset and dark_mode columns from users table."""
    op.drop_column('users', 'dark_mode')
    op.drop_column('users', 'theme_preset')

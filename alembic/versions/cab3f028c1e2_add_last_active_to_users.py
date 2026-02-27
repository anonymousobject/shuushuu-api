"""add last_active to users

Revision ID: cab3f028c1e2
Revises: 68c9d8c0a3c2
Create Date: 2026-02-27 09:25:27.580935

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cab3f028c1e2'
down_revision: str | Sequence[str] | None = '68c9d8c0a3c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add last_active column to users table."""
    op.add_column('users', sa.Column('last_active', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove last_active column from users table."""
    op.drop_column('users', 'last_active')

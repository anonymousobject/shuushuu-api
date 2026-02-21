"""add password reset fields

Revision ID: c8dc7007b860
Revises: c0cb8f931041
Create Date: 2026-02-20 22:35:10.819942

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8dc7007b860'
down_revision: str | Sequence[str] | None = 'c0cb8f931041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('password_reset_token', sa.String(64), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('password_reset_sent_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('password_reset_expires_at', sa.DateTime(), nullable=True),
    )
    op.create_index('idx_password_reset_token', 'users', ['password_reset_token'])


def downgrade() -> None:
    op.drop_index('idx_password_reset_token', table_name='users')
    op.drop_column('users', 'password_reset_expires_at')
    op.drop_column('users', 'password_reset_sent_at')
    op.drop_column('users', 'password_reset_token')

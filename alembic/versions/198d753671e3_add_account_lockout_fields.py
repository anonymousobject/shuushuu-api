"""Add account lockout fields

Revision ID: 198d753671e3
Revises: e4c0f75b08cb
Create Date: 2025-11-13 21:25:48.884549

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '198d753671e3'
down_revision: Union[str, Sequence[str], None] = 'e4c0f75b08cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TABLE users
    # ADD COLUMN failed_login_attempts INT DEFAULT 0,
    # ADD COLUMN lockout_until TIMESTAMP NULL;
    op.add_column(
        'users',
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'users',
        sa.Column('lockout_until', sa.DateTime(), nullable=True),
    )

def downgrade() -> None:
    """Downgrade schema."""
    # ALTER TABLE users
    # DROP COLUMN failed_login_attempts,
    # DROP COLUMN lockout_until;
    op.drop_column('users', 'lockout_until')
    op.drop_column('users', 'failed_login_attempts')

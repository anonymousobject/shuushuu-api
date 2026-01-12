"""add_email_verification

Revision ID: 7c37394cf3fb
Revises: tag_search_and_popularity
Create Date: 2025-12-15 07:35:40.061975

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '7c37394cf3fb'
down_revision: str | Sequence[str] | None = '5721ccce6a85'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add email verification fields
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('email_verification_token', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
    op.add_column('users', sa.Column('email_verification_sent_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('email_verification_expires_at', sa.DateTime(), nullable=True))

    # Create index for token lookup
    op.create_index('idx_email_verification_token', 'users', ['email_verification_token'], unique=False)

    # Remove legacy activation key
    op.drop_column('users', 'actkey')

    # Grandfather existing users as verified
    op.execute("UPDATE users SET email_verified = 1 WHERE active = 1")


def downgrade() -> None:
    """Downgrade schema."""
    # Restore legacy activation key
    op.add_column('users', sa.Column('actkey', mysql.VARCHAR(charset='utf8mb3', collation='utf8mb3_general_ci', length=32), server_default=sa.text("''"), nullable=False))

    # Remove index
    op.drop_index('idx_email_verification_token', table_name='users')

    # Remove email verification fields
    op.drop_column('users', 'email_verification_expires_at')
    op.drop_column('users', 'email_verification_sent_at')
    op.drop_column('users', 'email_verification_token')
    op.drop_column('users', 'email_verified')

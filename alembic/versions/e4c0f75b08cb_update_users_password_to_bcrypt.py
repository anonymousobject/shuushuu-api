"""update_users_password_to_bcrypt

Revision ID: e4c0f75b08cb
Revises: 176f47a1d07f
Create Date: 2025-11-11 21:40:25.783898

This migration updates the users table to support bcrypt password hashing.

IMPORTANT: This migration does NOT migrate existing passwords from MD5+salt to bcrypt.
You have two options:

1. Force password reset: Users must reset their passwords to use the new system
2. Dual authentication: Keep old MD5+salt fields temporarily and migrate gradually

The current implementation:
- Extends password field to 255 chars (bcrypt needs 60+ chars)
- Keeps old password/salt fields for backward compatibility
- Your auth code should check bcrypt first, fall back to MD5+salt if needed

To complete migration:
- Update your login code to migrate MD5 passwords to bcrypt on successful login
- After all users migrated, run another migration to drop old password/salt columns
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4c0f75b08cb'
down_revision: Union[str, Sequence[str], None] = '176f47a1d07f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Extend password field to support bcrypt hashes (60 chars needed, 255 for safety)
    # Old MD5 hashes are 40 chars, so this is safe
    op.alter_column('users', 'password',
                    existing_type=sa.String(length=40),
                    type_=sa.String(length=255),
                    existing_nullable=False)

    # Add new column to track password type (for migration period)
    # Values: 'md5' (legacy), 'bcrypt' (new)
    op.add_column('users', sa.Column('password_type', sa.String(length=10),
                                     nullable=False, server_default='md5'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove password type tracking column
    op.drop_column('users', 'password_type')

    # Revert password field length
    # WARNING: This will fail if any bcrypt hashes exist (they're longer than 40 chars)
    op.alter_column('users', 'password',
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=40),
                    existing_nullable=False)

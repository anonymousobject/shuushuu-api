"""rename_sha1_password_type_from_md5

Revision ID: 78fa9b978b3b
Revises: f8e2a1f956eb
Create Date: 2026-03-08 18:51:02.763827

The original migration that added password_type used 'md5' as the default for
all legacy users. However, the actual hashing algorithm for the vast majority of
accounts is SHA1+salt (40-char hex digest). True unsalted MD5 hashes (32-char)
exist for a small number of very old accounts.

This migration corrects the label for SHA1+salt accounts:
  - LENGTH(password) = 40 AND salt != '' → password_type = 'sha1'
  - LENGTH(password) = 32 AND salt = ''  → stays 'md5' (genuine unsalted MD5)

After this migration the login code handles each type explicitly:
  - 'bcrypt' → direct bcrypt verification
  - 'sha1'   → SHA1+salt verification, migrates to bcrypt on success
  - 'md5'    → verification intentionally unsupported (insecure legacy format); user is prompted to reset
"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '78fa9b978b3b'
down_revision: str | Sequence[str] | None = 'f8e2a1f956eb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename SHA1+salt accounts from password_type='md5' to password_type='sha1'."""
    op.execute(
        """
        UPDATE users
        SET password_type = 'sha1'
        WHERE password_type = 'md5'
          AND LENGTH(password) = 40
          AND salt != ''
        """
    )


def downgrade() -> None:
    """Revert SHA1+salt accounts back to password_type='md5'."""
    op.execute(
        """
        UPDATE users
        SET password_type = 'md5'
        WHERE password_type = 'sha1'
          AND LENGTH(password) = 40
          AND salt != ''
        """
    )

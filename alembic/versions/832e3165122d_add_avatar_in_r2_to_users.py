"""add avatar_in_r2 to users

Revision ID: 832e3165122d
Revises: 6f286ed3c418
Create Date: 2026-05-08 13:40:56.544377

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '832e3165122d'
down_revision: str | Sequence[str] | None = '6f286ed3c418'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add avatar_in_r2 to users.

    Tracks whether the avatar file referenced by users.avatar exists in R2.
    Default 0 — backfill is a separate one-shot run via
    `scripts/r2_sync.py avatars-backfill`.

    Uses ALGORITHM=INSTANT, LOCK=NONE so the migration is metadata-only on
    InnoDB (MariaDB 12) — no table rewrite, no row locks.
    """
    op.execute(
        "ALTER TABLE users ADD COLUMN avatar_in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Remove avatar_in_r2 column."""
    op.drop_column("users", "avatar_in_r2")

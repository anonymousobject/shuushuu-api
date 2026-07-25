"""add users thumb_size

Revision ID: b6f974207eb7
Revises: 7d98087eabcb
Create Date: 2026-07-24 22:51:54.328572

"""
from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6f974207eb7'
down_revision: str | Sequence[str] | None = '7d98087eabcb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the thumb_size grid thumbnail size preference (CSS px: 220, 320 or 440).

    Default 220 preserves the current grid rendering for every existing row.
    Uses ALGORITHM=INSTANT, LOCK=NONE so the migration is metadata-only on
    InnoDB — no table rewrite and no row locks over the users table. Plain
    op.add_column leaves the algorithm to the server, which can silently fall
    back to a locking rewrite; stating it explicitly makes the guarantee real.
    """
    op.execute(
        "ALTER TABLE users ADD COLUMN thumb_size INT NOT NULL DEFAULT 220, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Drop the thumb_size column.

    Mirrors the upgrade's metadata-only ALTER so the rollback path is
    non-locking too.
    """
    op.execute(
        "ALTER TABLE users DROP COLUMN thumb_size, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

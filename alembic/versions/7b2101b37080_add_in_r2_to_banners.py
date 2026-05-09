"""add in_r2 to banners

Revision ID: 7b2101b37080
Revises: 832e3165122d
Create Date: 2026-05-08 13:41:28.364492

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b2101b37080'
down_revision: str | Sequence[str] | None = '832e3165122d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add in_r2 to banners.

    Tracks whether all of full_image / left_image / middle_image / right_image
    referenced by this row exist in R2. Default 0 — backfill is a separate
    one-shot run via `scripts/r2_sync.py banners-backfill`.
    """
    op.execute(
        "ALTER TABLE banners ADD COLUMN in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Remove in_r2 column.

    Mirror the upgrade's metadata-only ALTER (ALGORITHM=INSTANT, LOCK=NONE)
    so the rollback path is non-locking too.
    """
    op.execute(
        "ALTER TABLE banners DROP COLUMN in_r2, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

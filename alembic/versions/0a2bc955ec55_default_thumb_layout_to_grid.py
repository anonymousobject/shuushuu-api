"""default thumb_layout to grid

Revision ID: 0a2bc955ec55
Revises: e380b541ec9f
Create Date: 2026-08-14 08:21:24.412218

"""
from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0a2bc955ec55'
down_revision: str | Sequence[str] | None = 'e380b541ec9f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Point the column default at grid (1) to match the model default.

    An anonymous visitor sees grid (the frontend resolves a missing
    preference to grid), so a list default meant creating an account silently
    switched the layout. Only new rows are affected: existing rows keep what
    they hold, because among recently active users who have demonstrably
    changed some other display preference, list is still the majority — the
    stored 0s are not all inherited defaults (FE #309).

    Metadata-only: SET DEFAULT rewrites the table definition, never the rows.
    Stated explicitly so the migration fails loudly rather than silently
    falling back to a locking rewrite of the users table.
    """
    op.execute(
        "ALTER TABLE users ALTER COLUMN thumb_layout SET DEFAULT 1, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Restore the legacy list default."""
    op.execute(
        "ALTER TABLE users ALTER COLUMN thumb_layout SET DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

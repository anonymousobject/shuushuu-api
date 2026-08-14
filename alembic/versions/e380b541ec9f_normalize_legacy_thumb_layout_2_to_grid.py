"""normalize legacy thumb_layout 2 to grid

Revision ID: e380b541ec9f
Revises: 40205fe4b1b6
Create Date: 2026-08-14 08:21:22.950451

"""
from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e380b541ec9f'
down_revision: str | Sequence[str] | None = '40205fe4b1b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Collapse legacy thumb_layout=2 rows onto grid (1).

    thumb_layout is documented and validated as 0=list, 1=grid — the API
    rejects anything else (app/schemas/user.py validate_boolean_prefs) — but
    ~10% of rows carry a 2 inherited from the old site. The frontend's
    view-mode store reads any non-zero value as grid, so those users already
    see grid; the settings radios, however, test `=== 1` and `=== 0`, leaving
    Display Preferences rendered with neither option selected and no way to
    see the current state. Writing 1 preserves the layout they already get
    and makes the stored value match the documented domain.
    """
    op.execute("UPDATE users SET thumb_layout = 1 WHERE thumb_layout = 2")


def downgrade() -> None:
    # Irreversible: a repaired row is indistinguishable from one that has
    # always said 1. Intentionally a no-op.
    pass

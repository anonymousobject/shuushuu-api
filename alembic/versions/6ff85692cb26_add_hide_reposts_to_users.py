"""add hide_reposts to users

Revision ID: 6ff85692cb26
Revises: 1cdaf1ec0250
Create Date: 2026-06-05 22:14:59.531115

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ff85692cb26'
down_revision: str | Sequence[str] | None = '1cdaf1ec0250'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the hide_reposts preference column (0=show reposts, 1=hide). INSTANT/NONE for zero-downtime."""
    op.execute(
        "ALTER TABLE users ADD COLUMN hide_reposts TINYINT(1) NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )


def downgrade() -> None:
    """Drop the hide_reposts column."""
    op.execute(
        "ALTER TABLE users DROP COLUMN hide_reposts, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

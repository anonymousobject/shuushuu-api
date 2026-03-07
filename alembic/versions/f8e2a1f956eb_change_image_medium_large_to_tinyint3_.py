"""change_image_medium_large_to_tinyint3_for_variant_status

Revision ID: f8e2a1f956eb
Revises: b50f77d51a12
Create Date: 2026-03-06 23:10:52.478679

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8e2a1f956eb'
down_revision: str | Sequence[str] | None = 'b50f77d51a12'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change medium/large columns from tinyint(1) to tinyint(3).

    tinyint(1) is treated as boolean by MySQL/MariaDB drivers, which would
    coerce the PENDING value (2) back to True (1=READY), breaking variant
    status tracking. tinyint(3) stores the same range but is returned as int.
    """
    op.execute("ALTER TABLE images MODIFY COLUMN `medium` TINYINT(3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE images MODIFY COLUMN `large` TINYINT(3) NOT NULL DEFAULT 0")


def downgrade() -> None:
    """Revert medium/large columns back to tinyint(1)."""
    op.execute("ALTER TABLE images MODIFY COLUMN `medium` TINYINT(1) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE images MODIFY COLUMN `large` TINYINT(1) NOT NULL DEFAULT 0")

"""merge tag_suggestion and main branches

Revision ID: b6fa39469acf
Revises: 9efa03a1b318, 9fe6ac38cb5c
Create Date: 2026-01-12 22:01:58.580503

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6fa39469acf'
down_revision: str | Sequence[str] | None = ('9efa03a1b318', '9fe6ac38cb5c')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge ml-tag-suggestions and source-url heads

Revision ID: 25cad69549de
Revises: b61cc2bcee90, ee16c4f335b0
Create Date: 2026-07-08 10:20:44.175915

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25cad69549de'
down_revision: str | Sequence[str] | None = ('b61cc2bcee90', 'ee16c4f335b0')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

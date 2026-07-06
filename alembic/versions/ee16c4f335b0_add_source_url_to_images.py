"""add source_url to images

Revision ID: ee16c4f335b0
Revises: 528091e4fac9
Create Date: 2026-07-06 12:01:37.527988

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ee16c4f335b0'
down_revision: str | Sequence[str] | None = '528091e4fac9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("images", sa.Column("source_url", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    op.drop_column("images", "source_url")

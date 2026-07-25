"""add users thumb_size

Revision ID: b6f974207eb7
Revises: 7d98087eabcb
Create Date: 2026-07-24 22:51:54.328572

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b6f974207eb7'
down_revision: str | Sequence[str] | None = '7d98087eabcb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("thumb_size", sa.Integer(), nullable=False, server_default="220"),
    )


def downgrade() -> None:
    op.drop_column("users", "thumb_size")

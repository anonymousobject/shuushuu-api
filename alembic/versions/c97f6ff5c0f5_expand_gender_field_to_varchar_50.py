"""expand gender field to varchar 50

Revision ID: c97f6ff5c0f5
Revises: e66f8043bc60
Create Date: 2026-01-03 21:54:12.612086

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c97f6ff5c0f5'
down_revision: str | Sequence[str] | None = 'e66f8043bc60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Expand gender field from VARCHAR(1) to VARCHAR(50) for free-form input."""
    op.alter_column(
        "users",
        "gender",
        type_=sa.String(50),
        existing_type=sa.String(1),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Revert gender field back to VARCHAR(1)."""
    op.alter_column(
        "users",
        "gender",
        type_=sa.String(1),
        existing_type=sa.String(50),
        existing_nullable=False,
    )

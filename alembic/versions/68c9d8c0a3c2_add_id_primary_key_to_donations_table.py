"""add id primary key to donations table

Revision ID: 68c9d8c0a3c2
Revises: 47b415e78d5c
Create Date: 2026-02-27 08:35:27.144562

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '68c9d8c0a3c2'
down_revision: str | Sequence[str] | None = '47b415e78d5c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add auto-increment id primary key to donations table.

    The original table has no primary key. SQLModel requires one,
    so we add an auto-increment id column in a single statement
    to avoid duplicate key errors (all rows default to 0 otherwise).
    """
    op.execute(
        "ALTER TABLE donations ADD COLUMN id INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
    )


def downgrade() -> None:
    """Remove id primary key from donations table."""
    op.drop_column('donations', 'id')

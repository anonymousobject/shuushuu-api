"""add iqdb_hash to images

Revision ID: 6f286ed3c418
Revises: 2393655e2b22
Create Date: 2026-05-07 20:55:39.364447

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f286ed3c418'
down_revision: str | Sequence[str] | None = '2393655e2b22'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add iqdb_hash column to images.

    Captured at iqdb-rs index time; powers the hash-only similarity-search
    query path. Nullable because backfill is async; populate_iqdb.py
    --only-missing-hash fills it in for pre-feature rows.
    """
    op.add_column(
        "images",
        sa.Column("iqdb_hash", sa.String(length=533), nullable=True),
    )


def downgrade() -> None:
    """Drop iqdb_hash column from images."""
    op.drop_column("images", "iqdb_hash")

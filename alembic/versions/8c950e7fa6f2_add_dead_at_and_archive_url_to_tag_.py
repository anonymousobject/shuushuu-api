"""add dead_at and archive_url to tag_external_links

Revision ID: 8c950e7fa6f2
Revises: 78fa9b978b3b
Create Date: 2026-03-11 14:15:25.923256

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c950e7fa6f2'
down_revision: str | Sequence[str] | None = '78fa9b978b3b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dead_at and archive_url columns to tag_external_links."""
    op.add_column(
        "tag_external_links",
        sa.Column("dead_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tag_external_links",
        sa.Column("archive_url", sa.String(length=2000), nullable=True),
    )


def downgrade() -> None:
    """Remove dead_at and archive_url columns from tag_external_links."""
    op.drop_column("tag_external_links", "archive_url")
    op.drop_column("tag_external_links", "dead_at")

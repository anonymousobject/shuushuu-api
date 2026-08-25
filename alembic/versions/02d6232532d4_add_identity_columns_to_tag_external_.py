"""add identity columns to tag_external_links

Revision ID: 02d6232532d4
Revises: 6dda18b955d8
Create Date: 2026-08-01 18:15:26.573269

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02d6232532d4'
down_revision: str | Sequence[str] | None = '10eef13f525a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tag_external_links", sa.Column("site", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "tag_external_links", sa.Column("external_id", sa.String(length=128), nullable=True)
    )
    op.create_index(
        "idx_tag_external_links_site_external_id",
        "tag_external_links",
        ["site", "external_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "idx_tag_external_links_site_external_id", table_name="tag_external_links"
    )
    op.drop_column("tag_external_links", "external_id")
    op.drop_column("tag_external_links", "site")

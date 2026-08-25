"""add identity columns to tag_external_links

Revision ID: e20bac5f3ac3
Revises: 0001_pg_baseline
Create Date: 2026-08-24 21:35:44.526681

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e20bac5f3ac3"
down_revision: str | Sequence[str] | None = "0001_pg_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tag_external_links", sa.Column("site", sa.String(length=32), nullable=True))
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
    op.drop_index("idx_tag_external_links_site_external_id", table_name="tag_external_links")
    op.drop_column("tag_external_links", "external_id")
    op.drop_column("tag_external_links", "site")

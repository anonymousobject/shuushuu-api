"""add identity columns to tag_external_links

Revision ID: 02d6232532d4
Revises: 28e56662b975
Create Date: 2026-08-01 18:15:26.573269

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02d6232532d4'
down_revision: str | Sequence[str] | None = '28e56662b975'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ci_string columns (ADR-0008): identity lookups/dedupe are
    # case-insensitive on both dialects. On MariaDB that's the connection's
    # default utf8mb4_unicode_ci collation -- plain VARCHAR(n) is the correct
    # DDL here, identical to what ci_string(n) itself emits on this dialect.
    # The Postgres side (case-insensitivity via CITEXT) lives in the
    # alembic_pg/ companion migration e20bac5f3ac3.
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

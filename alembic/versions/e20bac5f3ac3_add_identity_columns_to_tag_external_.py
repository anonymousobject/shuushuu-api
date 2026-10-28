"""add identity columns to tag_external_links

Revision ID: e20bac5f3ac3
Revises: 0003_add_user_reference_fks
Create Date: 2026-08-24 21:35:44.526681

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e20bac5f3ac3"
down_revision: str | Sequence[str] | None = "0003_add_user_reference_fks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # CITEXT, not VARCHAR(n): site/external_id are ci_string columns (ADR-0008)
    # -- case-insensitive identity lookups/dedupe on Postgres, matching
    # MariaDB's default collation. citext has no length modifier, so the
    # length caps below are CHECK constraints instead (same pattern as
    # users.username / users.email / tags.title in the frozen baseline and
    # app/core/pg_schema.py's _LENGTH_CHECKS). The citext extension is already
    # created by 0001_pg_baseline, an ancestor of this migration.
    op.add_column("tag_external_links", sa.Column("site", CITEXT(), nullable=True))
    op.add_column("tag_external_links", sa.Column("external_id", CITEXT(), nullable=True))
    op.create_index(
        "idx_tag_external_links_site_external_id",
        "tag_external_links",
        ["site", "external_id"],
    )
    op.execute(
        "ALTER TABLE tag_external_links ADD CONSTRAINT ck_tag_external_links_site_len "
        "CHECK (char_length(site) <= 32)"
    )
    op.execute(
        "ALTER TABLE tag_external_links ADD CONSTRAINT ck_tag_external_links_external_id_len "
        "CHECK (char_length(external_id) <= 128)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # DROP COLUMN cascades to CHECK constraints defined on that column, so the
    # two ck_* constraints above need no explicit drop.
    op.drop_index("idx_tag_external_links_site_external_id", table_name="tag_external_links")
    op.drop_column("tag_external_links", "external_id")
    op.drop_column("tag_external_links", "site")

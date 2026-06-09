"""add position to tag_external_links

Revision ID: 528091e4fac9
Revises: f6ca8c400ab8
Create Date: 2026-06-09 08:52:05.887787

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '528091e4fac9'
down_revision: str | Sequence[str] | None = 'f6ca8c400ab8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable per-tag display order for external links.

    NULL means "not custom-ordered" — the read query falls back to a computed
    default (shuu-wiki links first, then by date_added). A drag-to-reorder sets
    explicit positions. No backfill: existing links keep NULL and get the default.
    """
    op.add_column(
        "tag_external_links",
        sa.Column("position", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tag_external_links", "position")

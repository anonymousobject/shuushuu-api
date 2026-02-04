"""add tag sort indexes

Revision ID: 12207ec6cc9b
Revises: fa6353e5de42
Create Date: 2026-02-03 22:24:07.331919

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '12207ec6cc9b'
down_revision: str | Sequence[str] | None = 'fa6353e5de42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add B-tree indexes on tags.usage_count and tags.title for sort performance.

    The tags table has ~229K rows. Without these indexes, ORDER BY + LIMIT + OFFSET
    on these columns causes a full filesort on every paginated request.
    """
    op.create_index('idx_tags_usage_count', 'tags', ['usage_count'], unique=False)
    op.create_index('idx_tags_title', 'tags', ['title'], unique=False)


def downgrade() -> None:
    """Remove sort indexes from tags table."""
    op.drop_index('idx_tags_title', table_name='tags')
    op.drop_index('idx_tags_usage_count', table_name='tags')

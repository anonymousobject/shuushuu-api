"""Add tag search and popularity features

Revision ID: tag_search_and_popularity
Revises: 4f775fd5dd18
Create Date: 2025-12-13 20:33:48.000000

This migration implements two major tag features:
1. Full-text search on tag titles (word-order independent matching)
2. Usage count caching with database triggers (for sorting by popularity)

The FULLTEXT index enables fuzzy searching to solve the Japanese character name
problem (e.g., searching "sakura kinomoto" finds "kinomoto sakura").

The usage_count column tracks how many images have each tag, with automatic
updates via triggers on tag_links INSERT/DELETE operations. This enables
fast sorting by tag popularity without expensive joins.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'tag_search_and_popularity'
down_revision: str | Sequence[str] | None = '4f775fd5dd18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add full-text search and popularity tracking to tags."""
    # 1. Add usage_count column to track tag popularity
    op.add_column('tags', sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'))

    # 2. Add FULLTEXT index for fuzzy search
    op.execute("""
        ALTER TABLE tags ADD FULLTEXT INDEX ft_tags_title (title)
    """)

    # 3. Create triggers to maintain usage_count automatically
    # Trigger for INSERT on tag_links
    op.execute("""
        CREATE TRIGGER trig_tag_links_insert AFTER INSERT ON tag_links
        FOR EACH ROW
        BEGIN
            UPDATE tags SET usage_count = usage_count + 1
            WHERE tag_id = NEW.tag_id;
        END
    """)

    # Trigger for DELETE on tag_links
    op.execute("""
        CREATE TRIGGER trig_tag_links_delete AFTER DELETE ON tag_links
        FOR EACH ROW
        BEGIN
            UPDATE tags SET usage_count = usage_count - 1
            WHERE tag_id = OLD.tag_id;
        END
    """)


def downgrade() -> None:
    """Remove tag search and popularity features."""
    # Drop triggers first
    op.execute("DROP TRIGGER IF EXISTS trig_tag_links_insert")
    op.execute("DROP TRIGGER IF EXISTS trig_tag_links_delete")

    # Drop FULLTEXT index
    op.execute("""
        ALTER TABLE tags DROP INDEX ft_tags_title
    """)

    # Drop usage_count column
    op.drop_column('tags', 'usage_count')

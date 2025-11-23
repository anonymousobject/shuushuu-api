"""add fulltext index to posts.post_text

Revision ID: 6f22c3978270
Revises: 8d66158eb568
Create Date: 2025-11-03 11:51:58.773420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f22c3978270'
down_revision: Union[str, Sequence[str], None] = '8d66158eb568'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add FULLTEXT index to posts.post_text for fast comment searching.

    This enables the /comments/search/text endpoint to use MySQL's native
    full-text search (MATCH ... AGAINST), which is generally faster than
    LIKE pattern matching on large datasets.

    The index supports:
    - Natural language search with relevance ranking
    - Boolean search with operators (+word, -word, "phrase", word*)
    - Word stemming and stop word filtering

    Performance impact:
    - Significantly speeds up text searches
    - Index size: ~50-70% of column data size
    - Slight overhead on INSERT/UPDATE operations
    """
    # Use raw SQL for MySQL-specific FULLTEXT index
    # Note: FULLTEXT indexes can only be created on TEXT/VARCHAR columns
    # and are available on InnoDB tables (MySQL 5.6+)
    op.create_index(
        'idx_post_text_fulltext',
        'posts',
        ['post_text'],
        mysql_prefix='FULLTEXT'
    )
    # op.execute(
    #     "CREATE FULLTEXT INDEX idx_post_text_fulltext ON posts(post_text)"
    # )


def downgrade() -> None:
    """
    Remove FULLTEXT index from posts.post_text.

    This will cause the /comments/search/text endpoint to fail unless
    the code is reverted to use LIKE pattern matching instead.
    """
    op.drop_index(
        'idx_post_text_fulltext',
        table_name='posts'
    )
    # op.execute(
    #     "DROP INDEX idx_post_text_fulltext ON posts"
    # )

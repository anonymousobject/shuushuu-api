"""add parent_comment_id with set null cascade

Revision ID: 35ccc78f773a
Revises: 7c37394cf3fb
Create Date: 2025-12-17 20:27:15.435116

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35ccc78f773a'
down_revision: str | Sequence[str] | None = '7c37394cf3fb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create parent_comment_id to be UNSIGNED INT to match post_id type
    # Use raw SQL because the column may or may not exist yet
    op.execute(
        'ALTER TABLE posts '
        'ADD COLUMN parent_comment_id INT(10) UNSIGNED NULL'
    )

    # Ensure index exists
    op.create_index(
        'idx_posts_parent_comment_id',
        'posts',
        ['parent_comment_id'],
        unique=False,
        if_not_exists=True
    )

    # Add foreign key constraint with SET NULL
    op.create_foreign_key(
        'fk_posts_parent_comment_id',
        'posts',
        'posts',
        ['parent_comment_id'],
        ['post_id'],
        ondelete='SET NULL',
        onupdate='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop foreign key
    op.drop_constraint(
        'fk_posts_parent_comment_id',
        'posts',
        type_='foreignkey'
    )

    # Drop index
    op.drop_index('idx_posts_parent_comment_id', table_name='posts')

    # Drop column
    op.drop_column('posts', 'parent_comment_id')

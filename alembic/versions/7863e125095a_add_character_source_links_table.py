"""add_character_source_links_table

Revision ID: 7863e125095a
Revises: c97f6ff5c0f5
Create Date: 2026-01-07 10:15:03.402066

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '7863e125095a'
down_revision: str | Sequence[str] | None = 'c97f6ff5c0f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'character_source_links',
        sa.Column('character_tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('source_tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=False),
        sa.Column('created_by_user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['character_tag_id'], ['tags.tag_id'],
            name='fk_character_source_links_character_tag_id',
            ondelete='CASCADE', onupdate='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_tag_id'], ['tags.tag_id'],
            name='fk_character_source_links_source_tag_id',
            ondelete='CASCADE', onupdate='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.user_id'],
            name='fk_character_source_links_created_by_user_id',
            ondelete='SET NULL', onupdate='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('character_tag_id', 'source_tag_id', name='unique_character_source')
    )
    op.create_index('idx_character_tag_id', 'character_source_links', ['character_tag_id'], unique=False)
    op.create_index('idx_source_tag_id', 'character_source_links', ['source_tag_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_source_tag_id', table_name='character_source_links')
    op.drop_index('idx_character_tag_id', table_name='character_source_links')
    op.drop_table('character_source_links')

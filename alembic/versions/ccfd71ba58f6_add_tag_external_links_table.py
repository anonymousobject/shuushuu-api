"""add tag_external_links table

Revision ID: ccfd71ba58f6
Revises: ec5c5fa4e3e5
Create Date: 2025-12-26 08:27:22.140606

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'ccfd71ba58f6'
down_revision: str | Sequence[str] | None = 'ec5c5fa4e3e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'tag_external_links',
        sa.Column('url', sa.String(length=2000), nullable=False),
        sa.Column('link_id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('date_added', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=False),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.tag_id'], name='fk_tag_external_links_tag_id', ondelete='CASCADE', onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('link_id'),
        sa.UniqueConstraint('tag_id', 'url', name='unique_tag_url')
    )
    op.create_index('idx_tag_id', 'tag_external_links', ['tag_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_tag_id', table_name='tag_external_links')
    op.drop_table('tag_external_links')

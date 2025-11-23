"""add_refresh_tokens_table

Revision ID: 176f47a1d07f
Revises: 8619a9fc7189
Create Date: 2025-11-11 21:39:33.095753

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '176f47a1d07f'
down_revision: Union[str, Sequence[str], None] = '8619a9fc7189'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create refresh_tokens table
    # Note: user_id must be UNSIGNED to match users.user_id
    op.create_table(
        'refresh_tokens',
        sa.Column('id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('user_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('token_hash', sa.String(length=255), nullable=False),
        sa.Column('family_id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked', mysql.TINYINT(1), nullable=False, server_default='0'),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('parent_token_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_refresh_tokens_user_id', onupdate='CASCADE', ondelete='CASCADE'),
    )

    # Create indexes
    op.create_index('idx_refresh_tokens_user_id', 'refresh_tokens', ['user_id'])
    op.create_index('idx_refresh_tokens_token_hash', 'refresh_tokens', ['token_hash'], unique=True)
    op.create_index('idx_refresh_tokens_family_id', 'refresh_tokens', ['family_id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop indexes
    op.drop_index('idx_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_index('idx_refresh_tokens_token_hash', table_name='refresh_tokens')
    op.drop_index('idx_refresh_tokens_user_id', table_name='refresh_tokens')

    # Drop table
    op.drop_table('refresh_tokens')

"""add tag suggestion tables

Revision ID: 9fe6ac38cb5c
Revises: d1e2f3a4b5c6
Create Date: 2025-12-05 07:26:08.640661

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '9fe6ac38cb5c'
down_revision: str | Sequence[str] | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create ml_model_versions table
    op.create_table(
        'ml_model_versions',
        sa.Column('version_id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('deployed_at', sa.DateTime(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('version_id'),
        sa.UniqueConstraint('model_name', 'version', name='uq_model_name_version')
    )
    op.create_index('ix_ml_model_versions_is_active', 'ml_model_versions', ['is_active'])
    op.create_index('ix_ml_model_versions_model_name', 'ml_model_versions', ['model_name'])

    # Create tag_mappings table
    op.create_table(
        'tag_mappings',
        sa.Column('mapping_id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('external_tag', sa.String(length=255), nullable=False),
        sa.Column('external_source', sa.Enum('danbooru', 'other', name='external_source_enum'), nullable=False),
        sa.Column('internal_tag_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('created_by_user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['internal_tag_id'], ['tags.tag_id']),
        sa.PrimaryKeyConstraint('mapping_id'),
        sa.UniqueConstraint('external_source', 'external_tag', name='uq_external_source_tag')
    )
    op.create_index('ix_tag_mappings_internal_tag_id', 'tag_mappings', ['internal_tag_id'])

    # Create tag_suggestions table
    op.create_table(
        'tag_suggestions',
        sa.Column('suggestion_id', mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column('image_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('tag_id', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('model_source', sa.Enum('custom_theme', 'danbooru', name='model_source_enum'), nullable=False),
        sa.Column('model_version', sa.String(length=50), nullable=False),
        sa.Column('status', sa.Enum('pending', 'approved', 'rejected', name='suggestion_status_enum'), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by_user_id', mysql.INTEGER(unsigned=True), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.image_id']),
        sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.tag_id']),
        sa.PrimaryKeyConstraint('suggestion_id'),
        sa.UniqueConstraint('image_id', 'tag_id', name='uq_image_tag')
    )
    op.create_index('ix_tag_suggestions_image_id', 'tag_suggestions', ['image_id'])
    op.create_index('ix_tag_suggestions_status', 'tag_suggestions', ['status'])
    op.create_index('ix_tag_suggestions_tag_id', 'tag_suggestions', ['tag_id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tag_suggestions table
    op.drop_index('ix_tag_suggestions_tag_id', table_name='tag_suggestions')
    op.drop_index('ix_tag_suggestions_status', table_name='tag_suggestions')
    op.drop_index('ix_tag_suggestions_image_id', table_name='tag_suggestions')
    op.drop_table('tag_suggestions')

    # Drop tag_mappings table
    op.drop_index('ix_tag_mappings_internal_tag_id', table_name='tag_mappings')
    op.drop_table('tag_mappings')

    # Drop ml_model_versions table
    op.drop_index('ix_ml_model_versions_model_name', table_name='ml_model_versions')
    op.drop_index('ix_ml_model_versions_is_active', table_name='ml_model_versions')
    op.drop_table('ml_model_versions')

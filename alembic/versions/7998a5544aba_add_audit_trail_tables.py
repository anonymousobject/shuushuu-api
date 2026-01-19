"""add_audit_trail_tables

Creates the tag_audit_log and image_status_history tables for tracking
changes to tag metadata and image statuses.

Revision ID: 7998a5544aba
Revises: d1f00dc589f0
Create Date: 2026-01-19 14:17:14.164386

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7998a5544aba"
down_revision: str | Sequence[str] | None = "d1f00dc589f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create audit trail tables."""

    # =========================================================================
    # 1. Create tag_audit_log table
    # =========================================================================

    op.create_table(
        "tag_audit_log",
        sa.Column(
            "id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True
        ),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        # Rename fields
        sa.Column("old_title", sa.String(128), nullable=True),
        sa.Column("new_title", sa.String(128), nullable=True),
        # Type change fields
        sa.Column("old_type", sa.Integer(), nullable=True),
        sa.Column("new_type", sa.Integer(), nullable=True),
        # Alias change fields (FK to tags.tag_id)
        sa.Column("old_alias_of", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("new_alias_of", mysql.INTEGER(unsigned=True), nullable=True),
        # Parent/inheritance change fields (FK to tags.tag_id)
        sa.Column("old_parent_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("new_parent_id", mysql.INTEGER(unsigned=True), nullable=True),
        # Character-source link fields (FK to tags.tag_id)
        sa.Column("character_tag_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("source_tag_id", mysql.INTEGER(unsigned=True), nullable=True),
        # User who made the change
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=True),
        # Timestamp
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("current_timestamp()"),
        ),
        # Foreign key constraints
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_tag_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_tag_audit_log_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["old_alias_of"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_old_alias_of",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["new_alias_of"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_new_alias_of",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["old_parent_id"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_old_parent_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["new_parent_id"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_new_parent_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["character_tag_id"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_character_tag_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_tag_id"],
            ["tags.tag_id"],
            name="fk_tag_audit_log_source_tag_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )

    # Create indexes for tag_audit_log
    op.create_index(
        "idx_tag_audit_log_tag_id", "tag_audit_log", ["tag_id"], unique=False
    )
    op.create_index(
        "idx_tag_audit_log_user_id", "tag_audit_log", ["user_id"], unique=False
    )
    op.create_index(
        "idx_tag_audit_log_action_type", "tag_audit_log", ["action_type"], unique=False
    )
    op.create_index(
        "idx_tag_audit_log_created_at", "tag_audit_log", ["created_at"], unique=False
    )
    op.create_index(
        "idx_tag_audit_log_character_tag_id",
        "tag_audit_log",
        ["character_tag_id"],
        unique=False,
    )
    op.create_index(
        "idx_tag_audit_log_source_tag_id",
        "tag_audit_log",
        ["source_tag_id"],
        unique=False,
    )

    # =========================================================================
    # 2. Create image_status_history table
    # =========================================================================

    op.create_table(
        "image_status_history",
        sa.Column(
            "id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True
        ),
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("old_status", sa.Integer(), nullable=False),
        sa.Column("new_status", sa.Integer(), nullable=False),
        # User who made the change (nullable for system actions)
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=True),
        # Timestamp
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("current_timestamp()"),
        ),
        # Foreign key constraints
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_image_status_history_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_image_status_history_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )

    # Create indexes for image_status_history
    op.create_index(
        "idx_image_status_history_image_id",
        "image_status_history",
        ["image_id"],
        unique=False,
    )
    op.create_index(
        "idx_image_status_history_user_id",
        "image_status_history",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_image_status_history_created_at",
        "image_status_history",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop audit trail tables.

    Note: op.drop_table() automatically drops all indexes and foreign keys
    along with the table in MySQL/MariaDB. Explicit drop_index calls would
    fail with "Cannot drop index: needed in a foreign key constraint".
    """
    op.drop_table("image_status_history")
    op.drop_table("tag_audit_log")

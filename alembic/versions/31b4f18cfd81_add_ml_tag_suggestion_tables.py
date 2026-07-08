"""add ml tag suggestion tables

Revision ID: 31b4f18cfd81
Revises: 528091e4fac9
Create Date: 2026-06-12 10:48:56.163101

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "31b4f18cfd81"
down_revision: str | Sequence[str] | None = "528091e4fac9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ml_tag_suggestions and tag_mappings tables."""
    op.create_table(
        "tag_mappings",
        sa.Column("mapping_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_tag", sa.String(length=255), nullable=False),
        sa.Column("internal_tag_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["internal_tag_id"],
            ["tags.tag_id"],
            name="fk_tag_mappings_internal_tag_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mapping_id"),
        sa.UniqueConstraint("external_tag", name="unique_external_tag"),
    )

    op.create_table(
        "ml_tag_suggestions",
        sa.Column("suggestion_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="ml_suggestion_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by_user_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_ml_tag_suggestions_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            name="fk_ml_tag_suggestions_tag_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.user_id"],
            name="fk_ml_tag_suggestions_reviewed_by_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("suggestion_id"),
        sa.UniqueConstraint("image_id", "tag_id", name="unique_ml_suggestion_image_tag"),
    )
    op.create_index("idx_ml_suggestion_status", "ml_tag_suggestions", ["status"])


def downgrade() -> None:
    """Drop ml_tag_suggestions and tag_mappings tables."""
    op.drop_table("ml_tag_suggestions")
    op.drop_table("tag_mappings")

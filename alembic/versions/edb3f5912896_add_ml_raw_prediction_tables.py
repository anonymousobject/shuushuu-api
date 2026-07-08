"""add ml raw prediction tables

Revision ID: edb3f5912896
Revises: 31b4f18cfd81
Create Date: 2026-06-17 19:22:06.522340

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "edb3f5912896"
down_revision: str | Sequence[str] | None = "31b4f18cfd81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ml_models, ml_external_tags, and ml_raw_predictions tables."""
    op.create_table(
        "ml_models",
        sa.Column("id", mysql.SMALLINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="unique_ml_model_name"),
    )
    op.create_table(
        "ml_external_tags",
        sa.Column("id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="unique_ml_external_tag_name"),
    )
    op.create_table(
        "ml_raw_predictions",
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("model_id", mysql.SMALLINT(unsigned=True), nullable=False),
        sa.Column("external_tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_ml_raw_pred_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ml_models.id"],
            name="fk_ml_raw_pred_model_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["external_tag_id"],
            ["ml_external_tags.id"],
            name="fk_ml_raw_pred_external_tag_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("image_id", "model_id", "external_tag_id"),
    )


def downgrade() -> None:
    """Drop ml_raw_predictions, ml_external_tags, and ml_models tables."""
    op.drop_table("ml_raw_predictions")
    op.drop_table("ml_external_tags")
    op.drop_table("ml_models")

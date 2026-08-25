"""add character_source_link_pictures table

Revision ID: 40205fe4b1b6
Revises: 6dda18b955d8
Create Date: 2026-08-09 18:28:36.833700

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '40205fe4b1b6'
down_revision: str | Sequence[str] | None = '6dda18b955d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "character_source_link_pictures",
        sa.Column("link_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("crop_x", sa.Float(), nullable=False),
        sa.Column("crop_y", sa.Float(), nullable=False),
        sa.Column("crop_w", sa.Float(), nullable=False),
        sa.Column("crop_h", sa.Float(), nullable=False),
        sa.Column("set_by_user_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column(
            "set_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("link_id"),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["character_source_links.id"],
            name="fk_cslink_pictures_link_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_cslink_pictures_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"],
            ["users.user_id"],
            name="fk_cslink_pictures_set_by_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )
    op.create_index(
        "idx_cslink_pictures_image_id", "character_source_link_pictures", ["image_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_cslink_pictures_image_id", table_name="character_source_link_pictures")
    op.drop_table("character_source_link_pictures")

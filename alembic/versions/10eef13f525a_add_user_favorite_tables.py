"""add user favorite tables

Revision ID: 10eef13f525a
Revises: 0a2bc955ec55
Create Date: 2026-08-18 07:36:53.410876

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '10eef13f525a'
down_revision: str | Sequence[str] | None = '0a2bc955ec55'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_favorite_links",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("link_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "link_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"],
            name="fk_user_favorite_links_user_id",
            ondelete="CASCADE", onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["link_id"], ["character_source_links.id"],
            name="fk_user_favorite_links_link_id",
            ondelete="CASCADE", onupdate="CASCADE",
        ),
    )
    op.create_index("idx_user_favorite_links_link_id", "user_favorite_links", ["link_id"])

    op.create_table(
        "user_favorite_tags",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "tag_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"],
            name="fk_user_favorite_tags_user_id",
            ondelete="CASCADE", onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.tag_id"],
            name="fk_user_favorite_tags_tag_id",
            ondelete="CASCADE", onupdate="CASCADE",
        ),
    )
    op.create_index("idx_user_favorite_tags_tag_id", "user_favorite_tags", ["tag_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_user_favorite_tags_tag_id", table_name="user_favorite_tags")
    op.drop_table("user_favorite_tags")
    op.drop_index("idx_user_favorite_links_link_id", table_name="user_favorite_links")
    op.drop_table("user_favorite_links")

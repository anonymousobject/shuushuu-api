"""add user banner preferences

Revision ID: e8e9d4e6b553
Revises: 12207ec6cc9b
Create Date: 2026-02-06 07:17:40.310625

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'e8e9d4e6b553'
down_revision: str | Sequence[str] | None = '12207ec6cc9b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Narrow banners.size enum from (small,medium,large) to (small,large)
    op.execute("UPDATE banners SET size = 'small' WHERE size = 'medium'")
    op.execute(
        "ALTER TABLE banners MODIFY COLUMN size ENUM('small','large') NOT NULL DEFAULT 'small'"
    )

    op.create_table(
        "user_banner_preferences",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "preferred_size",
            sa.Enum("small", "large", name="bannersize"),
            nullable=False,
            server_default="small",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    op.create_table(
        "user_banner_pins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "size",
            sa.Enum("small", "large", name="bannersize"),
            nullable=False,
        ),
        sa.Column("theme", sa.VARCHAR(5), nullable=False),
        sa.Column("banner_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["banner_id"],
            ["banners.banner_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    op.create_index(
        "uq_user_size_theme",
        "user_banner_pins",
        ["user_id", "size", "theme"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("user_banner_pins")
    op.drop_table("user_banner_preferences")

    # Restore banners.size enum to include medium
    op.execute(
        "ALTER TABLE banners "
        "MODIFY COLUMN size ENUM('small','medium','large') NOT NULL DEFAULT 'medium'"
    )

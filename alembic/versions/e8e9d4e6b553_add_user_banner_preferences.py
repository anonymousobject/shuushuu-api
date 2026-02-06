"""add user banner preferences

Revision ID: e8e9d4e6b553
Revises: 12207ec6cc9b
Create Date: 2026-02-06 07:17:40.310625

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8e9d4e6b553'
down_revision: str | Sequence[str] | None = '12207ec6cc9b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_banner_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "preferred_size",
            sa.Enum("small", "medium", "large", name="bannersize"),
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
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "size",
            sa.Enum("small", "medium", "large", name="bannersize"),
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

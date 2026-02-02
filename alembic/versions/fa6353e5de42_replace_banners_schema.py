"""replace banners schema

Revision ID: fa6353e5de42
Revises: 9c92a1686d79
Create Date: 2026-02-01 14:42:42.442809

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa6353e5de42'
down_revision: str | Sequence[str] | None = '9c92a1686d79'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Legacy banners table is not preserved (separate DB from PHP site)
    op.execute("DROP TABLE IF EXISTS banners")

    op.create_table(
        "banners",
        sa.Column("banner_id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column(
            "size",
            sa.Enum("small", "medium", "large", name="bannersize"),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("full_image", sa.String(length=255), nullable=True),
        sa.Column("left_image", sa.String(length=255), nullable=True),
        sa.Column("middle_image", sa.String(length=255), nullable=True),
        sa.Column("right_image", sa.String(length=255), nullable=True),
        sa.Column("supports_dark", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("supports_light", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_index(
        "idx_active_dark_size",
        "banners",
        ["active", "supports_dark", "size"],
    )
    op.create_index(
        "idx_active_light_size",
        "banners",
        ["active", "supports_light", "size"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS banners")

    # Recreate the legacy schema (pre-rotating banners)
    op.create_table(
        "banners",
        sa.Column("banner_id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("author", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("leftext", sa.String(length=3), nullable=False, server_default="png"),
        sa.Column("midext", sa.String(length=3), nullable=False, server_default="png"),
        sa.Column("rightext", sa.String(length=3), nullable=False, server_default="png"),
        sa.Column("full", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "date",
            sa.TIMESTAMP(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

"""image_metadata_history: public audit of miscmeta/source_url edits

Revision ID: 0005_image_metadata_history
Revises: 0004_tag_search_fold
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_image_metadata_history"
down_revision: str | Sequence[str] | None = "0004_tag_search_fold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "image_metadata_history",
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.String(length=2000), nullable=True),
        sa.Column("new_value", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_image_metadata_history_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_image_metadata_history_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )
    op.create_index(
        "idx_image_metadata_history_image_id", "image_metadata_history", ["image_id"]
    )
    op.create_index("idx_image_metadata_history_user_id", "image_metadata_history", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("image_metadata_history")

"""add user_tag_affinity table

Revision ID: 12bf25199415
Revises: 25cad69549de
Create Date: 2026-07-09 22:58:03.151928

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '12bf25199415'
down_revision: str | Sequence[str] | None = '25cad69549de'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # No FKs: this table is fully rebuilt nightly via atomic swap
    # (CREATE TABLE ... LIKE drops FKs anyway).
    op.create_table(
        "user_tag_affinity",
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("pool_cnt", sa.Integer(), nullable=False),
        sa.Column("fav_count", sa.Integer(), nullable=False),
        sa.Column("upload_count", sa.Integer(), nullable=False),
        sa.Column("rated_count", sa.Integer(), nullable=False),
        sa.Column("rating_avg", sa.Float(), nullable=True),
        sa.Column("lift", sa.Float(), nullable=True),
        sa.Column("rating_delta", sa.Float(), nullable=True),
        sa.Column("affinity", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id", "tag_id"),
    )
    op.create_index("idx_user_tag_affinity_lookup", "user_tag_affinity", ["user_id", "affinity"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_user_tag_affinity_lookup", table_name="user_tag_affinity")
    op.drop_table("user_tag_affinity")

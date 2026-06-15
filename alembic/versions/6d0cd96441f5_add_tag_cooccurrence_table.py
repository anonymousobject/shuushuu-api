"""add tag_cooccurrence table

Revision ID: 6d0cd96441f5
Revises: 528091e4fac9
Create Date: 2026-06-14 21:58:12.679661

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '6d0cd96441f5'
down_revision: str | Sequence[str] | None = '528091e4fac9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # No FKs: this table is fully rebuilt weekly via atomic swap (CREATE TABLE ... LIKE drops FKs anyway).
    op.create_table(
        "tag_cooccurrence",
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("related_tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("related_type", sa.SmallInteger(), nullable=False),
        sa.Column("cooccur_count", sa.Integer(), nullable=False),
        sa.Column("lift", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("tag_id", "related_tag_id"),
    )
    op.create_index("idx_tag_cooccurrence_lookup", "tag_cooccurrence", ["tag_id", "lift"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_tag_cooccurrence_lookup", table_name="tag_cooccurrence")
    op.drop_table("tag_cooccurrence")

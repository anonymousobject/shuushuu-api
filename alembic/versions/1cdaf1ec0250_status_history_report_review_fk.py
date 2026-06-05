"""status_history report review fk

Revision ID: 1cdaf1ec0250
Revises: 4a93ca80f7f6
Create Date: 2026-06-04 14:41:26.288558

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '1cdaf1ec0250'
down_revision: str | Sequence[str] | None = '4a93ca80f7f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable report_id/review_id FKs to image_status_history."""
    op.add_column("image_status_history", sa.Column("report_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.add_column("image_status_history", sa.Column("review_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.create_index(
        "idx_image_status_history_report_id", "image_status_history", ["report_id"]
    )
    op.create_index(
        "idx_image_status_history_review_id", "image_status_history", ["review_id"]
    )
    op.create_foreign_key(
        "fk_image_status_history_report_id",
        "image_status_history",
        "image_reports",
        ["report_id"],
        ["report_id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "fk_image_status_history_review_id",
        "image_status_history",
        "image_reviews",
        ["review_id"],
        ["review_id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Drop report_id/review_id FKs from image_status_history."""
    op.drop_constraint(
        "fk_image_status_history_review_id", "image_status_history", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_image_status_history_report_id", "image_status_history", type_="foreignkey"
    )
    op.drop_index("idx_image_status_history_review_id", table_name="image_status_history")
    op.drop_index("idx_image_status_history_report_id", table_name="image_status_history")
    op.drop_column("image_status_history", "review_id")
    op.drop_column("image_status_history", "report_id")

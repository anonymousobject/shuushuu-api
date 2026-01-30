"""add_comment_reports

Revision ID: 9c92a1686d79
Revises: 2903e62e325f
Create Date: 2026-01-30 08:09:23.366666

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '9c92a1686d79'
down_revision: str | Sequence[str] | None = '2903e62e325f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Note: comment_id, user_id, reviewed_by must be UNSIGNED to match
    # posts.post_id and users.user_id types
    op.create_table(
        "comment_reports",
        sa.Column("report_id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("comment_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("category", sa.Integer(), nullable=True),
        sa.Column("reason_text", sa.String(1000), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.Column("reviewed_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("report_id"),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["posts.post_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )
    op.create_index(
        "idx_comment_reports_comment_id", "comment_reports", ["comment_id"]
    )
    op.create_index("idx_comment_reports_user_id", "comment_reports", ["user_id"])
    op.create_index(
        "idx_comment_reports_reviewed_by", "comment_reports", ["reviewed_by"]
    )
    op.create_index(
        "idx_comment_reports_status_category", "comment_reports", ["status", "category"]
    )
    op.create_index(
        "idx_comment_reports_pending_per_user",
        "comment_reports",
        ["comment_id", "user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_comment_reports_pending_per_user", table_name="comment_reports")
    op.drop_index("idx_comment_reports_status_category", table_name="comment_reports")
    op.drop_index("idx_comment_reports_reviewed_by", table_name="comment_reports")
    op.drop_index("idx_comment_reports_user_id", table_name="comment_reports")
    op.drop_index("idx_comment_reports_comment_id", table_name="comment_reports")
    op.drop_table("comment_reports")

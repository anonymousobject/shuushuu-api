"""add tag suggestions table and admin notes

Revision ID: e66f8043bc60
Revises: ccfd71ba58f6
Create Date: 2025-12-28 09:32:29.849163

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e66f8043bc60"
down_revision: str | Sequence[str] | None = "ccfd71ba58f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create image_report_tag_suggestions table
    op.create_table(
        "image_report_tag_suggestions",
        sa.Column(
            "suggestion_id",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            autoincrement=True,
        ),
        sa.Column("report_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["image_reports.report_id"],
            name="fk_suggestions_report_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            name="fk_suggestions_tag_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("suggestion_id"),
        sa.UniqueConstraint("report_id", "tag_id", name="unique_report_tag"),
    )
    op.create_index(
        "idx_report_id", "image_report_tag_suggestions", ["report_id"], unique=False
    )
    op.create_index(
        "idx_tag_id", "image_report_tag_suggestions", ["tag_id"], unique=False
    )
    op.create_index(
        "idx_accepted", "image_report_tag_suggestions", ["accepted"], unique=False
    )

    # Add admin_notes column to image_reports
    op.add_column("image_reports", sa.Column("admin_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove admin_notes column from image_reports
    op.drop_column("image_reports", "admin_notes")

    # Drop image_report_tag_suggestions table
    op.drop_index("idx_accepted", table_name="image_report_tag_suggestions")
    op.drop_index("idx_tag_id", table_name="image_report_tag_suggestions")
    op.drop_index("idx_report_id", table_name="image_report_tag_suggestions")
    op.drop_table("image_report_tag_suggestions")

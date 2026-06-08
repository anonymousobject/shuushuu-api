"""add description columns to tag_audit_log

Revision ID: f6ca8c400ab8
Revises: 6ff85692cb26
Create Date: 2026-06-08 14:36:59.594209

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6ca8c400ab8'
down_revision: str | Sequence[str] | None = '6ff85692cb26'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add old_desc/new_desc columns so description edits can be audit-logged."""
    op.add_column(
        "tag_audit_log",
        sa.Column("old_desc", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "tag_audit_log",
        sa.Column("new_desc", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tag_audit_log", "new_desc")
    op.drop_column("tag_audit_log", "old_desc")

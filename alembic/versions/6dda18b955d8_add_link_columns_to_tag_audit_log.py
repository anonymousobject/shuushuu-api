"""add link columns to tag_audit_log

Revision ID: 6dda18b955d8
Revises: acc9162c52ae
Create Date: 2026-07-31 21:07:24.602801

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6dda18b955d8'
down_revision: str | Sequence[str] | None = 'acc9162c52ae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add external-link columns so link add/remove/dead/archive can be audited."""
    op.add_column(
        "tag_audit_log",
        sa.Column("link_url", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "tag_audit_log",
        sa.Column("old_archive_url", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "tag_audit_log",
        sa.Column("new_archive_url", sa.String(length=2000), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tag_audit_log", "new_archive_url")
    op.drop_column("tag_audit_log", "old_archive_url")
    op.drop_column("tag_audit_log", "link_url")

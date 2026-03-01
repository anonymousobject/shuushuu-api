"""convert all tables from utf8mb3 to utf8mb4

Revision ID: 92f9d7890c30
Revises: cab3f028c1e2
Create Date: 2026-03-01 14:19:55.845256

"""
from typing import Sequence

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '92f9d7890c30'
down_revision: str | Sequence[str] | None = 'cab3f028c1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_all_tables() -> list[str]:
    """Discover tables dynamically so the list never drifts from the actual schema."""
    inspector = inspect(op.get_bind())
    tables = inspector.get_table_names()
    tables.sort()
    return tables


def upgrade() -> None:
    """Convert all tables from utf8mb3 to utf8mb4 to support full Unicode (emoji etc)."""
    op.execute("ALTER DATABASE CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

    for table in _get_all_tables():
        op.execute(
            f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )


def downgrade() -> None:
    """Revert all tables from utf8mb4 back to utf8mb3."""
    for table in reversed(_get_all_tables()):
        op.execute(
            f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci"
        )

    op.execute("ALTER DATABASE CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci")

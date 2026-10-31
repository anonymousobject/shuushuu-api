"""markup_version

Revision ID: 0008_markup_version
Revises: 0007_forum_import_provenance
Create Date: 2026-10-31 12:00:00.000000

Every existing row keeps the original renderer (version 1); every later insert
gets the new one (version 2), whichever code path writes it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_markup_version"
down_revision: str | Sequence[str] | None = "0007_forum_import_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("posts", "forum_posts", "privmsgs")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        # server_default "1" backfills the existing rows, then the default
        # flips to "2" for everything inserted from here on.
        op.add_column(
            table,
            sa.Column("markup_version", sa.SmallInteger(), nullable=False, server_default="1"),
        )
        op.alter_column(table, "markup_version", server_default=sa.text("2"))


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TABLES:
        op.drop_column(table, "markup_version")

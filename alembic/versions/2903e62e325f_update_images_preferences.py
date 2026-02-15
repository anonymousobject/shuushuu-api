"""update_images_preferences

Update users.images_per_page to 20 and for all users and set show_all_images default to True.

Revision ID: 2903e62e325f
Revises: 7998a5544aba
Create Date: 2026-01-24 12:29:41.897063

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2903e62e325f'
down_revision: str | Sequence[str] | None = '7998a5544aba'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Update images_per_page to 20 for all users."""
    op.execute("UPDATE users SET images_per_page = 20")
    op.execute("ALTER TABLE users ALTER COLUMN images_per_page SET DEFAULT 20")
    op.execute("ALTER TABLE users ALTER COLUMN show_all_images SET DEFAULT TRUE")


def downgrade() -> None:
    """Revert images_per_page to the previous default."""
    op.execute("ALTER TABLE users ALTER COLUMN images_per_page SET DEFAULT 10")
    op.execute("ALTER TABLE users ALTER COLUMN show_all_images SET DEFAULT FALSE")

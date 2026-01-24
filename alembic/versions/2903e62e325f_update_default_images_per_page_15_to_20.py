"""update_default_images_per_page_15_to_20

Update users.images_per_page from 15 to 20 for all users who have the old default.

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
    """Update images_per_page from <= 15 to 20 for users with the old default."""
    op.execute("UPDATE users SET images_per_page = 20 WHERE images_per_page <= 15")


def downgrade() -> None:
    """Revert images_per_page from 20 back to 15 for affected users."""
    op.execute("UPDATE users SET images_per_page = 15 WHERE images_per_page = 20")

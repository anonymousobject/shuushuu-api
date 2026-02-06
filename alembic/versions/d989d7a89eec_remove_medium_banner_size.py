"""remove medium banner size

Revision ID: d989d7a89eec
Revises: e8e9d4e6b553
Create Date: 2026-02-06 15:59:25.643588

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd989d7a89eec'
down_revision: str | Sequence[str] | None = 'e8e9d4e6b553'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove 'medium' from the BannerSize enum across all tables.

    Converts any existing 'medium' rows to 'small' before narrowing the enum.
    """
    # Convert existing medium values to small
    op.execute("UPDATE banners SET size = 'small' WHERE size = 'medium'")
    op.execute(
        "UPDATE user_banner_preferences SET preferred_size = 'small' WHERE preferred_size = 'medium'"
    )
    op.execute("UPDATE user_banner_pins SET size = 'small' WHERE size = 'medium'")

    # Alter enum columns to remove 'medium'
    op.execute(
        "ALTER TABLE banners MODIFY COLUMN size ENUM('small','large') NOT NULL DEFAULT 'small'"
    )
    op.execute(
        "ALTER TABLE user_banner_preferences "
        "MODIFY COLUMN preferred_size ENUM('small','large') NOT NULL DEFAULT 'small'"
    )
    op.execute(
        "ALTER TABLE user_banner_pins "
        "MODIFY COLUMN size ENUM('small','large') NOT NULL"
    )


def downgrade() -> None:
    """Re-add 'medium' to the BannerSize enum (data is not restored)."""
    op.execute(
        "ALTER TABLE banners "
        "MODIFY COLUMN size ENUM('small','medium','large') NOT NULL DEFAULT 'medium'"
    )
    op.execute(
        "ALTER TABLE user_banner_preferences "
        "MODIFY COLUMN preferred_size ENUM('small','medium','large') NOT NULL DEFAULT 'small'"
    )
    op.execute(
        "ALTER TABLE user_banner_pins "
        "MODIFY COLUMN size ENUM('small','medium','large') NOT NULL"
    )

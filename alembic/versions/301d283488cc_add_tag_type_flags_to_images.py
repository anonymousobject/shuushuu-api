"""add tag-type flags to images

Revision ID: 301d283488cc
Revises: 2e5fae0fd9a5
Create Date: 2026-05-31 07:19:15.237178

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '301d283488cc'
down_revision: str | Sequence[str] | None = '2e5fae0fd9a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Metadata-only column add (INSTANT) on the 1.1M-row table.
    op.execute(
        "ALTER TABLE images "
        "ADD COLUMN has_theme BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_source BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_artist BOOLEAN NOT NULL DEFAULT 0, "
        "ADD COLUMN has_character BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )
    # Online index builds (INPLACE, non-blocking).
    for col in ("has_theme", "has_source", "has_artist", "has_character"):
        op.execute(
            f"CREATE INDEX idx_images_{col} ON images ({col}, image_id) "
            "ALGORITHM INPLACE LOCK NONE"
        )


def downgrade() -> None:
    for col in ("has_theme", "has_source", "has_artist", "has_character"):
        op.execute(f"DROP INDEX idx_images_{col} ON images")
    op.execute(
        "ALTER TABLE images "
        "DROP COLUMN has_theme, DROP COLUMN has_source, "
        "DROP COLUMN has_artist, DROP COLUMN has_character, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

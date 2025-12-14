"""Extend original_filename to 255 chars, rename tags.alias to tags.alias_of

Revision ID: 4f775fd5dd18
Revises: d1e2f3a4b5c6
Create Date: 2025-12-12 09:38:29.439373

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '4f775fd5dd18'
down_revision: str | Sequence[str] | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Extend original_filename to 255 chars.
    Rename tags.alias to tags.alias_of
    """
    op.alter_column(
        'images',
        'original_filename',
        existing_type=sa.VARCHAR(length=120),
        type_=sa.VARCHAR(length=255),
        existing_nullable=True,
    )
    # Rename tags.alias to tags.alias_of
    # Drop the self-referential FK and index first to avoid ALGORITHM=COPY failures
    # Drop any existing foreign keys/indexes that might be present under either name.
    for fk_name in ("fk_tags_alias", "fk_tags_alias_of"):
        try:
            op.drop_constraint(fk_name, "tags", type_="foreignkey")
        except Exception:
            # Some MySQL versions or pre-existing schema may not have a constraint with this name.
            pass
    try:
        op.drop_index("fk_tags_alias", table_name="tags")
    except Exception:
        # Some MySQL versions or pre-existing schema may not have an index with this name.
        pass

    op.alter_column(
        'tags',
        'alias',
        new_column_name='alias_of',
        existing_type=mysql.INTEGER(display_width=10, unsigned=True),
        existing_nullable=True,
    )

    # Ensure column is explicitly unsigned to match tag_id, then recreate FK and index
    op.alter_column(
        'tags',
        'alias_of',
        existing_type=mysql.INTEGER(display_width=10, unsigned=True),
        type_=mysql.INTEGER(display_width=10, unsigned=True),
        existing_nullable=True,
    )
    # Recreate foreign key and index pointing to the renamed column
    op.create_foreign_key(
        "fk_tags_alias_of",
        "tags",
        "tags",
        ["alias_of"],
        ["tag_id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    try:
        op.create_index("fk_tags_alias", "tags", ["alias_of"])
    except Exception:
        # If the index already exists (e.g. partial/failed previous runs) ignore.
        pass



def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'images',
        'original_filename',
        existing_type=sa.VARCHAR(length=255),
        type_=sa.VARCHAR(length=120),
        existing_nullable=True,
    )

    # Revert: drop FK and index on alias_of, rename back to alias, recreate FK/index
    # Drop both fk/index names created by this migration when present, and
    # be tolerant to pre-existing or partially applied states.
    for fk_name in ("fk_tags_alias", "fk_tags_alias_of"):
        try:
            op.drop_constraint(fk_name, "tags", type_="foreignkey")
        except Exception:
            pass
    try:
        op.drop_index("fk_tags_alias", table_name="tags")
    except Exception:
        pass

    op.alter_column(
        'tags',
        'alias_of',
        new_column_name='alias',
        existing_type=mysql.INTEGER(display_width=10, unsigned=True),
        existing_nullable=True,
    )

    op.alter_column(
        'tags',
        'alias',
        existing_type=mysql.INTEGER(display_width=10, unsigned=True),
        type_=mysql.INTEGER(display_width=10, unsigned=True),
        existing_nullable=True,
    )

    try:
        op.create_foreign_key(
            "fk_tags_alias",
            "tags",
            "tags",
            ["alias"],
            ["tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        )
    except Exception:
        # If the constraint already exists, ignore
        pass
    try:
        op.create_index("fk_tags_alias", "tags", ["alias"])
    except Exception:
        # If the index already exists (duplicate key name), ignore
        pass

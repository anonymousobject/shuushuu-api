"""Remove deprecated user fields
Remove user fields that will be retired.

Revision ID: b8df3d41cab8
Revises: 198d753671e3
Create Date: 2025-11-15 07:53:39.881284

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8df3d41cab8'
down_revision: Union[str, Sequence[str], None] = '198d753671e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop deprecated user preference columns
    op.drop_column("users", "show_all_posts")
    op.drop_column("users", "show_all_meta")
    op.drop_column("users", "show_email")
    op.drop_column("users", "show_ip")


def downgrade() -> None:
    """Downgrade schema."""
    # Recreate deprecated columns with sensible defaults
    op.add_column(
        "users",
        sa.Column("show_all_posts", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("show_all_meta", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("show_email", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("show_ip", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )

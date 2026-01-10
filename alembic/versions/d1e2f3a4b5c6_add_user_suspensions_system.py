"""Add user suspensions system and migrate from bans table

This migration:
1. Creates user_suspensions audit table (single source of truth)
2. Migrates all records from bans to user_suspensions
3. Sets users.active=0 for currently active bans
4. Drops the old bans table

Design: Users table keeps only 'active' field for fast lookups.
All suspension details are stored in user_suspensions table.

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2025-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create user_suspensions table (single source of truth)
    op.create_table(
        "user_suspensions",
        sa.Column("suspension_id", mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actioned_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column(
            "actioned_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=False,
        ),
        sa.Column("suspended_until", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("suspension_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_user_suspensions_user_id",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actioned_by"],
            ["users.user_id"],
            name="fk_user_suspensions_actioned_by",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
    )

    # Create indexes on user_suspensions
    op.create_index("idx_user_suspensions_user_id", "user_suspensions", ["user_id"])
    op.create_index("idx_user_suspensions_actioned_by", "user_suspensions", ["actioned_by"])
    op.create_index("idx_user_suspensions_actioned_at", "user_suspensions", ["actioned_at"])
    op.create_index(
        "idx_user_suspensions_user_id_action_actioned_at",
        "user_suspensions",
        ["user_id", "action", "actioned_at"]
    )

    # Migrate data from bans to user_suspensions
    connection = op.get_bind()

    # Check if bans table exists before trying to migrate
    inspector = sa.inspect(connection)
    if "bans" in inspector.get_table_names():
        # Migrate all ban records to user_suspensions
        # Legacy 'action' enum values like 'One Week Ban' are redundant with date/expires timestamps
        # Legacy 'None' action means warning (no suspension)
        # Set acknowledged_at = date so users aren't prompted to acknowledge old warnings
        connection.execute(
            sa.text("""
                INSERT INTO user_suspensions
                    (user_id, action, actioned_by, actioned_at, suspended_until, reason, acknowledged_at)
                SELECT
                    user_id,
                    CASE WHEN action = 'None' THEN 'warning' ELSE 'suspended' END as action,
                    banned_by as actioned_by,
                    date as actioned_at,
                    CASE WHEN action = 'None' THEN NULL ELSE expires END as suspended_until,
                    NULLIF(LEFT(CONCAT_WS(' | ', NULLIF(reason, ''), NULLIF(message, '')), 500), '') as reason,
                    date as acknowledged_at
                FROM bans
                ORDER BY ban_id
            """)
        )

        # Set users.active=0 for currently active bans
        # Active ban = expires IS NULL (permanent) OR expires > NOW()
        # Use EXISTS to properly handle users with multiple ban records
        connection.execute(
            sa.text("""
                UPDATE users u
                SET u.active = 0
                WHERE EXISTS (
                    SELECT 1 FROM bans b
                    WHERE b.user_id = u.user_id
                    AND (b.expires IS NULL OR b.expires > NOW())
                )
            """)
        )

        # Drop the old bans table
        op.drop_table("bans")


def downgrade() -> None:
    """Downgrade schema - recreate bans table."""
    # Recreate bans table
    op.create_table(
        "bans",
        sa.Column("ban_id", mysql.INTEGER(unsigned=True), nullable=False, autoincrement=True),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("message", sa.String(length=255), nullable=True),
        sa.Column("viewed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("date", sa.DateTime(), server_default=sa.text("current_timestamp()"), nullable=True),
        sa.Column("expires", sa.DateTime(), nullable=True),
        sa.Column("banned_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("ip", sa.String(length=15), nullable=True),
        sa.PrimaryKeyConstraint("ban_id"),
        sa.ForeignKeyConstraint(
            ["banned_by"],
            ["users.user_id"],
            name="fk_bans_banned_by",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_bans_user_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    # Restore indexes
    op.create_index("fk_bans_banned_by", "bans", ["banned_by"])
    op.create_index("fk_bans_user_id", "bans", ["user_id"])

    # Drop indexes from user_suspensions
    op.drop_index("idx_user_suspensions_actioned_at", table_name="user_suspensions")
    op.drop_index("idx_user_suspensions_actioned_by", table_name="user_suspensions")
    op.drop_index("idx_user_suspensions_user_id", table_name="user_suspensions")
    op.drop_index("idx_user_suspensions_user_id_action_actioned_at", table_name="user_suspensions")
    # Drop user_suspensions table
    op.drop_table("user_suspensions")

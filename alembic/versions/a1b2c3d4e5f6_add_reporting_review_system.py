"""Add reporting and review system tables and columns

This migration:
1. Updates image_reports table:
   - Renames 'open' to 'status' (converts 1→0 pending, 0→2 dismissed)
   - Renames 'text' to 'reason_text'
   - Renames 'date' to 'created_at'
   - Renames 'image_report_id' to 'report_id'
   - Adds reviewed_by, reviewed_at columns

2. Renames image_reviews to review_votes and updates it:
   - Renames 'image_review_id' to 'vote_id'
   - Adds review_id, comment, created_at columns

3. Creates new image_reviews table for review sessions

4. Creates admin_actions audit log table

5. Adds new permissions for report/review management

Revision ID: a1b2c3d4e5f6
Revises: 79e1a49d9e90
Create Date: 2025-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "79e1a49d9e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply reporting and review system changes."""

    # =========================================================================
    # 1. Update image_reports table
    # =========================================================================

    # Rename columns using raw SQL to preserve exact column types (MariaDB)
    op.execute("ALTER TABLE image_reports RENAME COLUMN `text` TO `reason_text`")
    op.execute("ALTER TABLE image_reports RENAME COLUMN `date` TO `created_at`")
    op.execute("ALTER TABLE image_reports RENAME COLUMN `image_report_id` TO `report_id`")

    # Drop the old 'open' index before renaming the column
    op.drop_index("open", table_name="image_reports")

    # Rename 'open' column to 'status' using raw SQL
    op.execute("ALTER TABLE image_reports RENAME COLUMN `open` TO `status`")

    # Convert existing values: open=1 → status=0 (pending), open=0 → status=2 (dismissed)
    # Must do this AFTER rename but BEFORE adding new index
    op.execute(
        """
        UPDATE image_reports
        SET status = CASE
            WHEN status = 1 THEN 0  -- pending
            WHEN status = 0 THEN 2  -- dismissed
            ELSE status
        END
        """
    )

    # Add new columns (use UNSIGNED to match users.user_id type)
    op.add_column(
        "image_reports",
        sa.Column("reviewed_by", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.add_column(
        "image_reports",
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )

    # Add foreign key for reviewed_by
    op.create_foreign_key(
        "fk_image_reports_reviewed_by",
        "image_reports",
        "users",
        ["reviewed_by"],
        ["user_id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    # Add indexes
    op.create_index("idx_image_reports_status", "image_reports", ["status"])
    op.create_index("fk_image_reports_reviewed_by", "image_reports", ["reviewed_by"])

    # =========================================================================
    # 2. Rename image_reviews to review_votes and update
    # =========================================================================

    # Rename the table
    op.rename_table("image_reviews", "review_votes")

    # Rename primary key column using raw SQL to preserve exact column type
    op.execute("ALTER TABLE review_votes RENAME COLUMN `image_review_id` TO `vote_id`")

    # Add new columns (review_id unsigned to match image_reviews.review_id)
    op.add_column(
        "review_votes",
        sa.Column("review_id", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.add_column(
        "review_votes",
        sa.Column("comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "review_votes",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("current_timestamp()"),
        ),
    )

    # Set created_at for existing rows
    op.execute("UPDATE review_votes SET created_at = NOW() WHERE created_at IS NULL")

    # Update foreign key constraint names (drop old, create new)
    # Must drop FKs first, then index, then create new index, then new FKs
    op.drop_constraint("fk_image_reviews_image_id", "review_votes", type_="foreignkey")
    op.drop_constraint("fk_image_reviews_user_id", "review_votes", type_="foreignkey")

    # Rename existing unique index (must be after FK drop, before new FK create)
    op.drop_index("image_id", table_name="review_votes")
    op.create_index(
        "idx_review_votes_image_user",
        "review_votes",
        ["image_id", "user_id"],
        unique=True,
    )

    # Now create new FKs
    op.create_foreign_key(
        "fk_review_votes_image_id",
        "review_votes",
        "images",
        ["image_id"],
        ["image_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "fk_review_votes_user_id",
        "review_votes",
        "users",
        ["user_id"],
        ["user_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # =========================================================================
    # 3. Create new image_reviews table for review sessions
    # =========================================================================

    op.create_table(
        "image_reviews",
        sa.Column("review_id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("source_report_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("initiated_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("review_type", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("extension_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("current_timestamp()"),
        ),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_image_reviews_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_report_id"],
            ["image_reports.report_id"],
            name="fk_image_reviews_source_report_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by"],
            ["users.user_id"],
            name="fk_image_reviews_initiated_by",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )

    op.create_index("fk_image_reviews_image_id", "image_reviews", ["image_id"])
    op.create_index(
        "fk_image_reviews_source_report_id", "image_reviews", ["source_report_id"]
    )
    op.create_index("fk_image_reviews_initiated_by", "image_reviews", ["initiated_by"])
    op.create_index("idx_image_reviews_status", "image_reviews", ["status"])
    op.create_index("idx_image_reviews_deadline", "image_reviews", ["deadline"])

    # Now add review_id FK to review_votes (after image_reviews table exists)
    op.create_foreign_key(
        "fk_review_votes_review_id",
        "review_votes",
        "image_reviews",
        ["review_id"],
        ["review_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_index("fk_review_votes_review_id", "review_votes", ["review_id"])

    # Unique index for new votes with review sessions
    # MariaDB treats NULL as distinct in unique indexes, so legacy votes
    # with review_id=NULL won't conflict with each other
    op.create_index(
        "idx_review_votes_review_user",
        "review_votes",
        ["review_id", "user_id"],
        unique=True,
    )

    # =========================================================================
    # 4. Create admin_actions audit log table
    # =========================================================================

    op.create_table(
        "admin_actions",
        sa.Column("action_id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("user_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("action_type", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("review_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("current_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_admin_actions_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["image_reports.report_id"],
            name="fk_admin_actions_report_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["image_reviews.review_id"],
            name="fk_admin_actions_review_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_admin_actions_image_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
    )

    op.create_index("fk_admin_actions_user_id", "admin_actions", ["user_id"])
    op.create_index("fk_admin_actions_report_id", "admin_actions", ["report_id"])
    op.create_index("fk_admin_actions_review_id", "admin_actions", ["review_id"])
    op.create_index("fk_admin_actions_image_id", "admin_actions", ["image_id"])
    op.create_index("idx_admin_actions_created_at", "admin_actions", ["created_at"])
    op.create_index("idx_admin_actions_action_type", "admin_actions", ["action_type"])

    # =========================================================================
    # 5. Add new permissions
    # =========================================================================

    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('report_view', 'View report triage queue')"
    )
    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('report_manage', 'Dismiss/action/escalate reports')"
    )
    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('review_view', 'View open reviews')"
    )
    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('review_start', 'Initiate appropriateness review')"
    )
    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('review_vote', 'Cast votes on reviews')"
    )
    op.execute(
        "INSERT INTO perms (title, `desc`) VALUES ('review_close_early', 'Close review before deadline')"
    )

    # =========================================================================
    # 6. Add new permissions to existing admin groups
    # =========================================================================

    # Insert permissions for 'mods' by looking up the group's id by title and avoid duplicates
    # Note: if the 'mods' group does not exist on a target instance, the SELECT will return
    # no rows and the INSERT will be a no-op — this keeps the migration safe across installs.
    op.execute(
        """
        INSERT INTO group_perms (group_id, perm_id, permvalue)
        SELECT g.group_id, p.perm_id, 1
        FROM perms p
        JOIN groups g ON g.title = 'mods'
        WHERE p.title IN (
            'image_tag_remove',
            'privmsg_view',
            'report_view',
            'report_manage',
            'review_view',
            'review_start',
            'review_vote',
            'review_close_early'
        )
        AND NOT EXISTS (
            SELECT 1 FROM group_perms gp WHERE gp.group_id = g.group_id AND gp.perm_id = p.perm_id
        )
        """
    )

    # Insert permissions for 'admins' by looking up the group's id by title and avoid duplicates
    op.execute(
        """
        INSERT INTO group_perms (group_id, perm_id, permvalue)
        SELECT g.group_id, p.perm_id, 1
        FROM perms p
        JOIN groups g ON g.title = 'admins'
        WHERE p.title IN (
            'image_tag_remove',
            'privmsg_view',
            'report_view',
            'report_manage',
            'review_view',
            'review_start',
            'review_vote',
            'review_close_early'
        )
        AND NOT EXISTS (
            SELECT 1 FROM group_perms gp WHERE gp.group_id = g.group_id AND gp.perm_id = p.perm_id
        )
        """
    )





def downgrade() -> None:
    """Revert reporting and review system changes."""

    # =========================================================================
    # 5. Remove permissions and related group assignments
    # =========================================================================

    # Remove group_perms for the perms we added by looking up group ids by title to avoid relying on hardcoded ids
    # Note: these DELETEs are safe to run even if the groups or permissions are missing; they
    # will simply delete zero rows if nothing matches (no-op).
    op.execute(
        """
        DELETE gp
        FROM group_perms gp
        JOIN perms p ON gp.perm_id = p.perm_id
        JOIN groups g ON gp.group_id = g.group_id
        WHERE g.title = 'mods' AND p.title IN (
            'image_tag_remove',
            'privmsg_view',
            'report_view',
            'report_manage',
            'review_view',
            'review_start',
            'review_vote',
            'review_close_early'
        )
        """
    )

    op.execute(
        """
        DELETE gp
        FROM group_perms gp
        JOIN perms p ON gp.perm_id = p.perm_id
        JOIN groups g ON gp.group_id = g.group_id
        WHERE g.title = 'admins' AND p.title IN (
            'image_tag_remove',
            'privmsg_view',
            'report_view',
            'report_manage',
            'review_view',
            'review_start',
            'review_vote',
            'review_close_early'
        )
        """
    )

    # Remove the perms themselves
    op.execute("DELETE FROM perms WHERE title = 'report_view'")
    op.execute("DELETE FROM perms WHERE title = 'report_manage'")
    op.execute("DELETE FROM perms WHERE title = 'review_view'")
    op.execute("DELETE FROM perms WHERE title = 'review_start'")
    op.execute("DELETE FROM perms WHERE title = 'review_vote'")
    op.execute("DELETE FROM perms WHERE title = 'review_close_early'")

    # =========================================================================
    # 4. Drop admin_actions table
    # =========================================================================

    op.drop_table("admin_actions")

    # =========================================================================
    # 3. Drop new image_reviews table
    # =========================================================================

    # First drop indexes and FK from review_votes
    op.drop_index("idx_review_votes_review_user", table_name="review_votes")
    op.drop_constraint("fk_review_votes_review_id", "review_votes", type_="foreignkey")
    op.drop_index("fk_review_votes_review_id", table_name="review_votes")

    op.drop_table("image_reviews")

    # =========================================================================
    # 2. Rename review_votes back to image_reviews
    # =========================================================================

    # Drop new columns
    op.drop_column("review_votes", "review_id")
    op.drop_column("review_votes", "comment")
    op.drop_column("review_votes", "created_at")

    # Restore foreign key names
    op.drop_constraint("fk_review_votes_image_id", "review_votes", type_="foreignkey")
    op.drop_constraint("fk_review_votes_user_id", "review_votes", type_="foreignkey")

    op.create_foreign_key(
        "fk_image_reviews_image_id",
        "review_votes",
        "images",
        ["image_id"],
        ["image_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "fk_image_reviews_user_id",
        "review_votes",
        "users",
        ["user_id"],
        ["user_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # Restore unique index name
    op.drop_index("idx_review_votes_image_user", table_name="review_votes")
    op.create_index(
        "image_id", "review_votes", ["image_id", "user_id"], unique=True
    )

    # Rename primary key column back
    op.alter_column(
        "review_votes",
        "vote_id",
        new_column_name="image_review_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )

    # Rename table back
    op.rename_table("review_votes", "image_reviews")

    # =========================================================================
    # 1. Revert image_reports changes
    # =========================================================================

    # Drop new columns and indexes
    op.drop_constraint(
        "fk_image_reports_reviewed_by", "image_reports", type_="foreignkey"
    )
    op.drop_index("fk_image_reports_reviewed_by", table_name="image_reports")
    op.drop_column("image_reports", "reviewed_by")
    op.drop_column("image_reports", "reviewed_at")

    # Revert status values: 0 (pending) → 1 (open), 2 (dismissed) → 0 (closed)
    op.execute(
        """
        UPDATE image_reports
        SET status = CASE
            WHEN status = 0 THEN 1  -- pending back to open=1
            WHEN status = 2 THEN 0  -- dismissed back to open=0
            ELSE status
        END
        """
    )

    # Drop status index
    op.drop_index("idx_image_reports_status", table_name="image_reports")

    # Rename 'status' back to 'open'
    op.alter_column(
        "image_reports",
        "status",
        new_column_name="open",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )

    # Recreate original index
    op.create_index("open", "image_reports", ["open"])

    # Rename 'report_id' back to 'image_report_id'
    op.alter_column(
        "image_reports",
        "report_id",
        new_column_name="image_report_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )

    # Rename 'created_at' back to 'date'
    op.alter_column(
        "image_reports",
        "created_at",
        new_column_name="date",
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )

    # Rename 'reason_text' back to 'text'
    op.alter_column(
        "image_reports",
        "reason_text",
        new_column_name="text",
        existing_type=sa.Text(),
        existing_nullable=True,
    )

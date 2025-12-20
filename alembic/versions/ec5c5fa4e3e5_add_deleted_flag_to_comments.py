"""add_deleted_flag_to_comments

Revision ID: ec5c5fa4e3e5
Revises: 2cd4e874e956
Create Date: 2025-12-18 23:51:27.417057

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec5c5fa4e3e5'
down_revision: str | Sequence[str] | None = '2cd4e874e956'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add deleted column with default False
    op.add_column(
        'posts',
        sa.Column('deleted', sa.Boolean(), nullable=False, server_default='0')
    )

    # Add index on deleted column for filtering
    op.create_index('idx_posts_deleted', 'posts', ['deleted'])

    # Mark existing "[deleted]" comments as deleted
    op.execute("""
        UPDATE posts
        SET deleted = TRUE
        WHERE post_text = '[deleted]'
    """)

    # Update triggers to fire on UPDATE as well as INSERT/DELETE
    # This allows soft-delete (UPDATE deleted=TRUE) to trigger counter decrements

    # Drop existing triggers
    op.execute("DROP TRIGGER IF EXISTS comments_after_insert")
    op.execute("DROP TRIGGER IF EXISTS comments_after_delete")

    # Recreate INSERT trigger
    op.execute("""
        CREATE TRIGGER comments_after_insert
        AFTER INSERT ON posts
        FOR EACH ROW
        BEGIN
            IF NEW.deleted = FALSE THEN
                UPDATE images SET posts = posts + 1 WHERE image_id = NEW.image_id;
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # Create UPDATE trigger for soft-delete
    op.execute("""
        CREATE TRIGGER comments_after_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.deleted = FALSE AND NEW.deleted = TRUE THEN
                UPDATE images SET posts = posts - 1 WHERE image_id = NEW.image_id;
                UPDATE users SET posts = posts - 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # Recreate DELETE trigger for hard-delete
    op.execute("""
        CREATE TRIGGER comments_after_delete
        AFTER DELETE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.deleted = FALSE THEN
                UPDATE images SET posts = posts - 1 WHERE image_id = OLD.image_id;
                UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
            END IF;
        END
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # Restore original triggers
    op.execute("DROP TRIGGER IF EXISTS comments_after_insert")
    op.execute("DROP TRIGGER IF EXISTS comments_after_update")
    op.execute("DROP TRIGGER IF EXISTS comments_after_delete")

    # Recreate original INSERT trigger
    op.execute("""
        CREATE TRIGGER comments_after_insert
        AFTER INSERT ON posts
        FOR EACH ROW
        BEGIN
            UPDATE images SET posts = posts + 1 WHERE image_id = NEW.image_id;
            UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
        END
    """)

    # Recreate original DELETE trigger
    op.execute("""
        CREATE TRIGGER comments_after_delete
        AFTER DELETE ON posts
        FOR EACH ROW
        BEGIN
            UPDATE images SET posts = posts - 1 WHERE image_id = OLD.image_id;
            UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
        END
    """)

    # Drop index and column
    op.drop_index('idx_posts_deleted', table_name='posts')
    op.drop_column('posts', 'deleted')

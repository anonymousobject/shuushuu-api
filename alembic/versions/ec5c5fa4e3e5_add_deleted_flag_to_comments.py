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

    # ========================================
    # Replace post triggers with soft-delete-aware versions
    # ========================================
    # Drop triggers from previous migration (2cd4e874e956) that don't know about `deleted`
    op.execute("DROP TRIGGER IF EXISTS images_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS images_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS images_posts_update")
    op.execute("DROP TRIGGER IF EXISTS users_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS users_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS users_posts_update")
    # Also drop any leftover triggers from earlier iterations
    op.execute("DROP TRIGGER IF EXISTS comments_after_insert")
    op.execute("DROP TRIGGER IF EXISTS comments_after_update")
    op.execute("DROP TRIGGER IF EXISTS comments_after_delete")

    # INSERT: only count non-deleted comments
    op.execute("""
        CREATE TRIGGER images_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        BEGIN
            IF NEW.deleted = 0 THEN
                UPDATE images
                SET posts = posts + 1, last_post = NEW.date
                WHERE image_id = NEW.image_id;
            END IF;
        END
    """)

    op.execute("""
        CREATE TRIGGER users_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        BEGIN
            IF NEW.deleted = 0 THEN
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # UPDATE: handle soft-delete flag changes AND image_id/user_id changes
    op.execute("""
        CREATE TRIGGER images_posts_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            -- Soft delete
            IF OLD.deleted = 0 AND NEW.deleted = 1 THEN
                UPDATE images
                SET posts = posts - 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = NEW.image_id AND deleted = 0)
                WHERE image_id = NEW.image_id;
            -- Undelete
            ELSEIF OLD.deleted = 1 AND NEW.deleted = 0 THEN
                UPDATE images
                SET posts = posts + 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = NEW.image_id AND deleted = 0)
                WHERE image_id = NEW.image_id;
            -- Comment moved between images
            ELSEIF OLD.image_id != NEW.image_id THEN
                UPDATE images
                SET posts = posts - 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = OLD.image_id AND deleted = 0)
                WHERE image_id = OLD.image_id;
                UPDATE images
                SET posts = posts + 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = NEW.image_id AND deleted = 0)
                WHERE image_id = NEW.image_id;
            END IF;
        END
    """)

    op.execute("""
        CREATE TRIGGER users_posts_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            -- Soft delete
            IF OLD.deleted = 0 AND NEW.deleted = 1 THEN
                UPDATE users SET posts = posts - 1 WHERE user_id = NEW.user_id;
            -- Undelete
            ELSEIF OLD.deleted = 1 AND NEW.deleted = 0 THEN
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            -- Comment moved between users
            ELSEIF OLD.user_id != NEW.user_id THEN
                UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # DELETE (hard-delete): only adjust if the row wasn't already soft-deleted
    op.execute("""
        CREATE TRIGGER images_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.deleted = 0 THEN
                UPDATE images
                SET posts = posts - 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = OLD.image_id AND deleted = 0)
                WHERE image_id = OLD.image_id;
            END IF;
        END
    """)

    op.execute("""
        CREATE TRIGGER users_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.deleted = 0 THEN
                UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
            END IF;
        END
    """)

    # Reinitialize counters excluding soft-deleted comments
    op.execute("""
        UPDATE images i
        SET posts = (SELECT COUNT(*) FROM posts p WHERE p.image_id = i.image_id AND p.deleted = 0),
            last_post = (SELECT MAX(date) FROM posts p WHERE p.image_id = i.image_id AND p.deleted = 0)
    """)

    op.execute("""
        UPDATE users u
        SET posts = (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.user_id AND p.deleted = 0)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop soft-delete-aware triggers
    op.execute("DROP TRIGGER IF EXISTS images_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS images_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS images_posts_update")
    op.execute("DROP TRIGGER IF EXISTS users_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS users_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS users_posts_update")

    # Restore simple triggers from 2cd4e874e956 (no deleted awareness)
    op.execute("""
        CREATE TRIGGER images_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        UPDATE images
        SET posts = posts + 1, last_post = NEW.date
        WHERE image_id = NEW.image_id
    """)

    op.execute("""
        CREATE TRIGGER images_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        UPDATE images
        SET posts = posts - 1,
            last_post = (SELECT MAX(date) FROM posts WHERE image_id = OLD.image_id)
        WHERE image_id = OLD.image_id
    """)

    op.execute("""
        CREATE TRIGGER images_posts_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.image_id != NEW.image_id THEN
                UPDATE images
                SET posts = posts - 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = OLD.image_id)
                WHERE image_id = OLD.image_id;
                UPDATE images
                SET posts = posts + 1,
                    last_post = (SELECT MAX(date) FROM posts WHERE image_id = NEW.image_id)
                WHERE image_id = NEW.image_id;
            END IF;
        END
    """)

    op.execute("""
        CREATE TRIGGER users_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id
    """)

    op.execute("""
        CREATE TRIGGER users_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id
    """)

    op.execute("""
        CREATE TRIGGER users_posts_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.user_id != NEW.user_id THEN
                UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # Drop index and column
    op.drop_index('idx_posts_deleted', table_name='posts')
    op.drop_column('posts', 'deleted')

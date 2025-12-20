"""add counter triggers and indexes for images and users

Revision ID: 2cd4e874e956
Revises: 35ccc78f773a
Create Date: 2025-12-17 21:44:39.693988

Adds triggers to automatically maintain counter fields:
- images.posts (comment count on image)
- images.favorites (favorite count on image)
- users.posts (comment count by user)
- users.image_posts (image upload count by user)
- users.favorites (favorite count by user)

Also adds indexes on these fields for efficient filtering.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cd4e874e956'
down_revision: str | Sequence[str] | None = '35ccc78f773a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ========================================
    # 1. ADD INDEXES
    # ========================================
    # Using raw SQL for IF NOT EXISTS support
    op.execute("CREATE INDEX IF NOT EXISTS idx_posts ON images (posts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_posts ON users (posts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_image_posts ON users (image_posts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_favorites ON users (favorites)")

    # ========================================
    # 2. IMAGES.POSTS TRIGGERS (comments on image)
    # ========================================
    # Drop old triggers from previous migration
    op.execute("DROP TRIGGER IF EXISTS posts_increment_on_insert")
    op.execute("DROP TRIGGER IF EXISTS posts_decrement_on_delete")
    op.execute("DROP TRIGGER IF EXISTS posts_update_on_image_change")

    # Create new triggers
    op.execute("DROP TRIGGER IF EXISTS images_posts_increment")
    op.execute("""
        CREATE TRIGGER images_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        UPDATE images SET posts = posts + 1 WHERE image_id = NEW.image_id
    """)

    op.execute("DROP TRIGGER IF EXISTS images_posts_decrement")
    op.execute("""
        CREATE TRIGGER images_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        UPDATE images SET posts = posts - 1 WHERE image_id = OLD.image_id
    """)

    op.execute("DROP TRIGGER IF EXISTS images_posts_update")
    op.execute("""
        CREATE TRIGGER images_posts_update
        AFTER UPDATE ON posts
        FOR EACH ROW
        BEGIN
            IF OLD.image_id != NEW.image_id THEN
                UPDATE images SET posts = posts - 1 WHERE image_id = OLD.image_id;
                UPDATE images SET posts = posts + 1 WHERE image_id = NEW.image_id;
            END IF;
        END
    """)

    # ========================================
    # 3. IMAGES.FAVORITES TRIGGERS (favorites on image)
    # ========================================
    op.execute("DROP TRIGGER IF EXISTS images_favorites_increment")
    op.execute("""
        CREATE TRIGGER images_favorites_increment
        AFTER INSERT ON favorites
        FOR EACH ROW
        UPDATE images SET favorites = favorites + 1 WHERE image_id = NEW.image_id
    """)

    op.execute("DROP TRIGGER IF EXISTS images_favorites_decrement")
    op.execute("""
        CREATE TRIGGER images_favorites_decrement
        AFTER DELETE ON favorites
        FOR EACH ROW
        UPDATE images SET favorites = favorites - 1 WHERE image_id = OLD.image_id
    """)

    op.execute("DROP TRIGGER IF EXISTS images_favorites_update")
    op.execute("""
        CREATE TRIGGER images_favorites_update
        AFTER UPDATE ON favorites
        FOR EACH ROW
        BEGIN
            IF OLD.image_id != NEW.image_id THEN
                UPDATE images SET favorites = favorites - 1 WHERE image_id = OLD.image_id;
                UPDATE images SET favorites = favorites + 1 WHERE image_id = NEW.image_id;
            END IF;
        END
    """)

    # ========================================
    # 4. USERS.POSTS TRIGGERS (comments by user)
    # ========================================
    op.execute("DROP TRIGGER IF EXISTS users_posts_increment")
    op.execute("""
        CREATE TRIGGER users_posts_increment
        AFTER INSERT ON posts
        FOR EACH ROW
        UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_posts_decrement")
    op.execute("""
        CREATE TRIGGER users_posts_decrement
        AFTER DELETE ON posts
        FOR EACH ROW
        UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_posts_update")
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

    # ========================================
    # 5. USERS.IMAGE_POSTS TRIGGERS (images uploaded by user)
    # ========================================
    op.execute("DROP TRIGGER IF EXISTS users_image_posts_increment")
    op.execute("""
        CREATE TRIGGER users_image_posts_increment
        AFTER INSERT ON images
        FOR EACH ROW
        UPDATE users SET image_posts = image_posts + 1 WHERE user_id = NEW.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_image_posts_decrement")
    op.execute("""
        CREATE TRIGGER users_image_posts_decrement
        AFTER DELETE ON images
        FOR EACH ROW
        UPDATE users SET image_posts = image_posts - 1 WHERE user_id = OLD.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_image_posts_update")
    op.execute("""
        CREATE TRIGGER users_image_posts_update
        AFTER UPDATE ON images
        FOR EACH ROW
        BEGIN
            IF OLD.user_id != NEW.user_id THEN
                UPDATE users SET image_posts = image_posts - 1 WHERE user_id = OLD.user_id;
                UPDATE users SET image_posts = image_posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # ========================================
    # 6. USERS.FAVORITES TRIGGERS (favorites made by user)
    # ========================================
    op.execute("DROP TRIGGER IF EXISTS users_favorites_increment")
    op.execute("""
        CREATE TRIGGER users_favorites_increment
        AFTER INSERT ON favorites
        FOR EACH ROW
        UPDATE users SET favorites = favorites + 1 WHERE user_id = NEW.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_favorites_decrement")
    op.execute("""
        CREATE TRIGGER users_favorites_decrement
        AFTER DELETE ON favorites
        FOR EACH ROW
        UPDATE users SET favorites = favorites - 1 WHERE user_id = OLD.user_id
    """)

    op.execute("DROP TRIGGER IF EXISTS users_favorites_update")
    op.execute("""
        CREATE TRIGGER users_favorites_update
        AFTER UPDATE ON favorites
        FOR EACH ROW
        BEGIN
            IF OLD.user_id != NEW.user_id THEN
                UPDATE users SET favorites = favorites - 1 WHERE user_id = OLD.user_id;
                UPDATE users SET favorites = favorites + 1 WHERE user_id = NEW.user_id;
            END IF;
        END
    """)

    # ========================================
    # 7. INITIALIZE COUNTERS FOR EXISTING DATA
    # ========================================
    # Images: posts and favorites
    op.execute("""
        UPDATE images i
        SET posts = (SELECT COUNT(*) FROM posts p WHERE p.image_id = i.image_id)
    """)

    op.execute("""
        UPDATE images i
        SET favorites = (SELECT COUNT(*) FROM favorites f WHERE f.image_id = i.image_id)
    """)

    # Users: posts, image_posts, and favorites
    op.execute("""
        UPDATE users u
        SET posts = (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.user_id)
    """)

    op.execute("""
        UPDATE users u
        SET image_posts = (SELECT COUNT(*) FROM images i WHERE i.user_id = u.user_id)
    """)

    op.execute("""
        UPDATE users u
        SET favorites = (SELECT COUNT(*) FROM favorites f WHERE f.user_id = u.user_id)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop all triggers
    # Images.posts
    op.execute("DROP TRIGGER IF EXISTS images_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS images_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS images_posts_update")

    # Images.favorites
    op.execute("DROP TRIGGER IF EXISTS images_favorites_increment")
    op.execute("DROP TRIGGER IF EXISTS images_favorites_decrement")
    op.execute("DROP TRIGGER IF EXISTS images_favorites_update")

    # Users.posts
    op.execute("DROP TRIGGER IF EXISTS users_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS users_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS users_posts_update")

    # Users.image_posts
    op.execute("DROP TRIGGER IF EXISTS users_image_posts_increment")
    op.execute("DROP TRIGGER IF EXISTS users_image_posts_decrement")
    op.execute("DROP TRIGGER IF EXISTS users_image_posts_update")

    # Users.favorites
    op.execute("DROP TRIGGER IF EXISTS users_favorites_increment")
    op.execute("DROP TRIGGER IF EXISTS users_favorites_decrement")
    op.execute("DROP TRIGGER IF EXISTS users_favorites_update")

    # Drop indexes (using raw SQL for IF EXISTS support)
    op.execute("DROP INDEX IF EXISTS idx_posts ON images")
    op.execute("DROP INDEX IF EXISTS idx_user_posts ON users")
    op.execute("DROP INDEX IF EXISTS idx_user_image_posts ON users")
    op.execute("DROP INDEX IF EXISTS idx_user_favorites ON users")

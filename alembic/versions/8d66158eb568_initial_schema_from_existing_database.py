"""Initial schema from existing database

Revision ID: 8d66158eb568
Revises:
Create Date: 2025-10-29 20:19:41.188630

This baseline migration creates the schema as it exists in the legacy PHP database.
For existing databases: run `alembic stamp head` instead of `alembic upgrade head`.
For new databases: run `alembic upgrade head` to create all tables.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d66158eb568"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial schema from legacy database dump."""
    # Disable FK checks during table creation to avoid ordering issues
    op.execute("SET FOREIGN_KEY_CHECKS=0")

    # ===== Independent tables (no FK dependencies) =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `banners` (
          `banner_id` smallint(4) unsigned NOT NULL AUTO_INCREMENT,
          `path` varchar(255) NOT NULL DEFAULT '',
          `author` varchar(255) NOT NULL DEFAULT '',
          `leftext` char(3) NOT NULL DEFAULT 'png',
          `midext` char(3) NOT NULL DEFAULT 'png',
          `rightext` char(3) NOT NULL DEFAULT 'png',
          `full` tinyint(1) NOT NULL DEFAULT 0,
          `event_id` int(11) unsigned NOT NULL DEFAULT 0,
          `active` tinyint(1) NOT NULL DEFAULT 1,
          `date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`banner_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `donations` (
          `date` datetime NOT NULL DEFAULT current_timestamp(),
          `user_id` int(10) unsigned DEFAULT NULL,
          `nick` varchar(30) DEFAULT NULL,
          `amount` int(3) DEFAULT NULL,
          KEY `idx_date` (`date`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `eva_theme` (
          `theme_id` int(11) NOT NULL AUTO_INCREMENT,
          `theme_content` longtext DEFAULT NULL,
          `active_month_from` tinyint(2) NOT NULL DEFAULT 0,
          `active_month_to` tinyint(2) NOT NULL DEFAULT 0,
          `active_day_from` tinyint(2) NOT NULL DEFAULT 0,
          `active_day_to` tinyint(2) NOT NULL DEFAULT 0,
          `active` tinyint(1) NOT NULL DEFAULT 0,
          `theme_name` varchar(255) NOT NULL DEFAULT '',
          `banner` varchar(255) NOT NULL DEFAULT '',
          PRIMARY KEY (`theme_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `groups` (
          `group_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `title` varchar(50) DEFAULT NULL,
          `desc` varchar(75) DEFAULT NULL,
          PRIMARY KEY (`group_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `image_ratings_avg` (
          `type` char(3) DEFAULT NULL,
          `avg` float DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `perms` (
          `perm_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `title` varchar(50) DEFAULT NULL,
          `desc` varchar(75) DEFAULT NULL,
          PRIMARY KEY (`perm_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `tips` (
          `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `tip` varchar(255) DEFAULT NULL,
          `type` int(1) NOT NULL DEFAULT 0,
          PRIMARY KEY (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci
    """)

    # ===== Core tables with self-references =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `users` (
          `user_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `forum_id` mediumint(8) unsigned DEFAULT NULL,
          `date_joined` datetime NOT NULL DEFAULT current_timestamp(),
          `last_login` datetime DEFAULT current_timestamp(),
          `active` tinyint(1) NOT NULL DEFAULT 0,
          `admin` tinyint(1) NOT NULL DEFAULT 0,
          `username` varchar(30) NOT NULL,
          `password` varchar(40) NOT NULL,
          `salt` char(16) NOT NULL,
          `timezone` decimal(5,2) NOT NULL DEFAULT 0.00,
          `email_pm_pref` tinyint(1) NOT NULL DEFAULT 1,
          `spoiler_warning_pref` tinyint(1) NOT NULL DEFAULT 1,
          `thumb_layout` tinyint(1) NOT NULL DEFAULT 0,
          `sorting_pref` varchar(100) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT 'image_id',
          `sorting_pref_order` varchar(10) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT 'DESC',
          `images_per_page` int(3) NOT NULL DEFAULT 10,
          `show_all_images` tinyint(1) NOT NULL DEFAULT 0,
          `show_all_meta` tinyint(1) NOT NULL DEFAULT 0,
          `show_all_posts` tinyint(1) NOT NULL DEFAULT 0,
          `show_ip` tinyint(1) NOT NULL DEFAULT 0,
          `bookmark` int(10) unsigned DEFAULT NULL,
          `posts` mediumint(8) unsigned NOT NULL DEFAULT 0,
          `image_posts` mediumint(8) NOT NULL DEFAULT 0,
          `favorites` int(10) unsigned NOT NULL DEFAULT 0,
          `email` varchar(120) NOT NULL,
          `show_email` tinyint(4) NOT NULL DEFAULT 0,
          `avatar` varchar(255) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT '',
          `avatar_type` tinyint(2) NOT NULL DEFAULT 0,
          `gender` char(1) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT '',
          `location` varchar(100) DEFAULT NULL,
          `website` varchar(100) DEFAULT NULL,
          `aim` varchar(50) DEFAULT NULL,
          `interests` varchar(255) DEFAULT NULL,
          `user_title` varchar(50) DEFAULT NULL,
          `actkey` varchar(32) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT '',
          `newpassword` varchar(40) DEFAULT NULL,
          `newsalt` char(16) DEFAULT NULL,
          `maximgperday` int(3) NOT NULL DEFAULT 15,
          `rating_ratio` float NOT NULL DEFAULT 0,
          `infected` tinyint(1) DEFAULT 0,
          `infected_by` int(11) unsigned NOT NULL DEFAULT 0,
          `date_infected` int(11) NOT NULL DEFAULT 0,
          `last_login_new` datetime DEFAULT NULL,
          PRIMARY KEY (`user_id`),
          UNIQUE KEY `username` (`username`),
          KEY `fk_bookmark` (`bookmark`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `images` (
          `image_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `user_id` int(10) unsigned NOT NULL,
          `date_added` datetime DEFAULT current_timestamp(),
          `useragent` varchar(255) NOT NULL DEFAULT '',
          `ip` varchar(15) NOT NULL DEFAULT '',
          `status_user_id` int(10) unsigned DEFAULT NULL,
          `status_updated` datetime DEFAULT NULL,
          `status` tinyint(2) NOT NULL DEFAULT 1,
          `locked` tinyint(1) NOT NULL DEFAULT 0,
          `last_updated` datetime DEFAULT NULL,
          `last_post` datetime DEFAULT NULL,
          `filename` varchar(120) DEFAULT NULL,
          `ext` varchar(10) NOT NULL,
          `md5_hash` varchar(32) NOT NULL DEFAULT '',
          `original_filename` varchar(120) DEFAULT NULL,
          `filesize` int(9) unsigned NOT NULL DEFAULT 0,
          `width` smallint(6) unsigned NOT NULL DEFAULT 0,
          `height` smallint(6) unsigned NOT NULL DEFAULT 0,
          `total_pixels` decimal(6,3) unsigned DEFAULT NULL,
          `medium` tinyint(1) NOT NULL DEFAULT 0,
          `large` tinyint(1) NOT NULL DEFAULT 0,
          `posts` smallint(4) unsigned NOT NULL DEFAULT 0,
          `favorites` smallint(4) unsigned NOT NULL DEFAULT 0,
          `caption` varchar(35) NOT NULL DEFAULT '',
          `image_source` varchar(255) DEFAULT NULL,
          `artist` varchar(200) DEFAULT NULL,
          `characters` text DEFAULT NULL,
          `miscmeta` varchar(255) DEFAULT NULL,
          `rating` float NOT NULL DEFAULT 0,
          `bayesian_rating` float NOT NULL DEFAULT 0,
          `num_ratings` int(4) unsigned NOT NULL DEFAULT 0,
          `replacement_id` int(10) unsigned DEFAULT NULL,
          `reviewed` tinyint(1) NOT NULL DEFAULT 0,
          `change_id` int(10) NOT NULL DEFAULT 0,
          PRIMARY KEY (`image_id`),
          KEY `change_id` (`change_id`),
          KEY `idx_bayesian_rating` (`bayesian_rating`),
          KEY `idx_top_images` (`num_ratings`),
          KEY `idx_total_pixels` (`total_pixels`),
          KEY `idx_favorites` (`favorites`),
          KEY `idx_filename` (`filename`),
          KEY `idx_status` (`status`),
          KEY `fk_images_user_id` (`user_id`),
          KEY `fk_images_status_user_id` (`status_user_id`),
          KEY `fk_images_replacement_id` (`replacement_id`),
          KEY `idx_last_post` (`last_post`),
          CONSTRAINT `fk_images_replacement_id` FOREIGN KEY (`replacement_id`) REFERENCES `images` (`image_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_images_status_user_id` FOREIGN KEY (`status_user_id`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_images_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `tags` (
          `tag_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `title` varchar(150) DEFAULT NULL,
          `desc` varchar(200) DEFAULT NULL,
          `alias` int(10) unsigned DEFAULT NULL,
          `inheritedfrom_id` int(10) unsigned DEFAULT NULL,
          `date_added` datetime NOT NULL DEFAULT current_timestamp(),
          `user_id` int(10) unsigned DEFAULT NULL,
          `type` tinyint(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (`tag_id`),
          KEY `type_alias` (`type`,`alias`),
          KEY `fk_tags_inheritedfrom_id` (`inheritedfrom_id`),
          KEY `fk_tags_user_id` (`user_id`),
          KEY `fk_tags_alias` (`alias`),
          CONSTRAINT `fk_tags_alias` FOREIGN KEY (`alias`) REFERENCES `tags` (`tag_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_tags_inheritedfrom_id` FOREIGN KEY (`inheritedfrom_id`) REFERENCES `tags` (`tag_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_tags_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    # ===== Add FK constraint for users.bookmark (circular dep with images) =====
    op.execute("""
        ALTER TABLE `users`
        ADD CONSTRAINT `fk_bookmark` FOREIGN KEY (`bookmark`) REFERENCES `images` (`image_id`) ON DELETE SET NULL ON UPDATE CASCADE
    """)

    # ===== Tables dependent on users =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `bans` (
          `ban_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `user_id` int(10) unsigned NOT NULL,
          `banned_by` int(10) unsigned DEFAULT NULL,
          `ip` varchar(15) DEFAULT NULL,
          `action` enum('None','One Week Ban','Two Week Ban','One Month Ban','Permanent Ban') DEFAULT NULL,
          `reason` tinytext DEFAULT NULL,
          `message` varchar(255) DEFAULT NULL,
          `viewed` tinyint(1) NOT NULL DEFAULT 0,
          `date` datetime DEFAULT current_timestamp(),
          `expires` datetime DEFAULT NULL,
          PRIMARY KEY (`ban_id`),
          KEY `fk_bans_user_id` (`user_id`),
          KEY `fk_bans_banned_by` (`banned_by`),
          CONSTRAINT `fk_bans_banned_by` FOREIGN KEY (`banned_by`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_bans_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `news` (
          `news_id` smallint(8) unsigned NOT NULL AUTO_INCREMENT,
          `user_id` int(10) unsigned NOT NULL,
          `title` varchar(128) DEFAULT NULL,
          `news_text` text DEFAULT NULL,
          `date` datetime DEFAULT current_timestamp(),
          `edited` datetime DEFAULT NULL,
          PRIMARY KEY (`news_id`),
          KEY `fk_news_user_id` (`user_id`),
          CONSTRAINT `fk_news_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `privmsgs` (
          `privmsg_id` int(11) NOT NULL AUTO_INCREMENT,
          `from_user_id` int(10) unsigned NOT NULL,
          `to_user_id` int(10) unsigned NOT NULL,
          `subject` varchar(255) NOT NULL DEFAULT '',
          `text` text DEFAULT NULL,
          `viewed` tinyint(1) NOT NULL DEFAULT 0,
          `from_del` tinyint(1) NOT NULL DEFAULT 0,
          `to_del` tinyint(1) NOT NULL DEFAULT 0,
          `type` tinyint(1) NOT NULL DEFAULT 1,
          `card` tinyint(1) NOT NULL DEFAULT 0,
          `cardpath` varchar(255) NOT NULL DEFAULT '',
          `date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`privmsg_id`),
          KEY `fk_privmsgs_from_user_id` (`from_user_id`),
          KEY `fk_privmsgs_to_user_id` (`to_user_id`),
          CONSTRAINT `fk_privmsgs_from_user_id` FOREIGN KEY (`from_user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_privmsgs_to_user_id` FOREIGN KEY (`to_user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `quicklinks` (
          `quicklink_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `user_id` int(10) unsigned DEFAULT NULL,
          `link` varchar(32) DEFAULT NULL,
          PRIMARY KEY (`quicklink_id`),
          KEY `fk_quicklinks_user_id` (`user_id`),
          CONSTRAINT `fk_quicklinks_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `user_sessions` (
          `session_id` varchar(50) NOT NULL DEFAULT '',
          `user_id` int(10) unsigned NOT NULL,
          `last_used` datetime NOT NULL DEFAULT current_timestamp(),
          `last_view_date` datetime DEFAULT current_timestamp(),
          `ip` varchar(16) NOT NULL DEFAULT '',
          `lastpage` varchar(200) DEFAULT NULL,
          `last_search` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`session_id`),
          KEY `ip` (`ip`),
          KEY `fk_user_sessions_user_id` (`user_id`),
          CONSTRAINT `fk_user_sessions_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    # ===== Junction tables for permissions =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `group_perms` (
          `group_id` int(10) unsigned NOT NULL,
          `perm_id` int(10) unsigned NOT NULL,
          `permvalue` tinyint(1) DEFAULT NULL,
          PRIMARY KEY (`group_id`,`perm_id`),
          KEY `fk_group_perms_perm_id` (`perm_id`),
          CONSTRAINT `fk_group_perms_group_id` FOREIGN KEY (`group_id`) REFERENCES `groups` (`group_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_group_perms_perm_id` FOREIGN KEY (`perm_id`) REFERENCES `perms` (`perm_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `user_groups` (
          `user_id` int(11) NOT NULL,
          `group_id` int(11) NOT NULL,
          PRIMARY KEY (`user_id`,`group_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `user_perms` (
          `user_id` int(11) NOT NULL,
          `perm_id` int(11) NOT NULL,
          `permvalue` tinyint(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (`user_id`,`perm_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    # ===== Tables dependent on images =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `favorites` (
          `user_id` int(10) unsigned NOT NULL,
          `image_id` int(10) unsigned NOT NULL,
          `fav_date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`user_id`,`image_id`),
          KEY `fk_favorites_image_id` (`image_id`),
          CONSTRAINT `fk_favorites_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_favorites_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `image_ratings` (
          `user_id` int(10) unsigned NOT NULL,
          `image_id` int(10) unsigned NOT NULL,
          `rating` tinyint(2) NOT NULL DEFAULT 0,
          `date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`user_id`,`image_id`),
          KEY `fk_image_ratings_image_id` (`image_id`),
          CONSTRAINT `fk_image_ratings_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_image_ratings_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `image_reports` (
          `image_report_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `image_id` int(10) unsigned NOT NULL,
          `user_id` int(10) unsigned NOT NULL,
          `open` tinyint(1) NOT NULL DEFAULT 1,
          `category` tinyint(3) unsigned DEFAULT NULL,
          `text` text DEFAULT NULL,
          `date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`image_report_id`),
          KEY `open` (`open`),
          KEY `fk_image_reports_user_id` (`user_id`),
          KEY `fk_image_reports_image_id` (`image_id`),
          CONSTRAINT `fk_image_reports_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_image_reports_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `image_reviews` (
          `image_review_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `image_id` int(10) unsigned DEFAULT NULL,
          `user_id` int(10) unsigned DEFAULT NULL,
          `vote` tinyint(1) DEFAULT NULL,
          PRIMARY KEY (`image_review_id`),
          UNIQUE KEY `image_id` (`image_id`,`user_id`),
          KEY `fk_image_reviews_user_id` (`user_id`),
          CONSTRAINT `fk_image_reviews_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_image_reviews_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `posts` (
          `post_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `image_id` int(10) unsigned DEFAULT NULL,
          `user_id` int(10) unsigned NOT NULL,
          `useragent` varchar(255) NOT NULL DEFAULT '',
          `ip` varchar(15) NOT NULL DEFAULT '',
          `date` datetime NOT NULL DEFAULT current_timestamp(),
          `last_updated` datetime DEFAULT NULL,
          `last_updated_user_id` int(10) unsigned DEFAULT NULL,
          `update_count` int(3) unsigned NOT NULL DEFAULT 0,
          `post_text` text NOT NULL,
          PRIMARY KEY (`post_id`),
          KEY `idx_date` (`date`),
          KEY `fk_posts_user_id` (`user_id`),
          KEY `fk_posts_image_id` (`image_id`),
          KEY `fk_posts_last_updated_user_id` (`last_updated_user_id`),
          CONSTRAINT `fk_posts_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_posts_last_updated_user_id` FOREIGN KEY (`last_updated_user_id`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_posts_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    # ===== Tables dependent on tags =====

    op.execute("""
        CREATE TABLE IF NOT EXISTS `tag_history` (
          `tag_history_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `image_id` int(10) unsigned DEFAULT NULL,
          `tag_id` int(10) unsigned DEFAULT NULL,
          `user_id` int(10) unsigned DEFAULT NULL,
          `action` char(1) DEFAULT NULL,
          `date` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`tag_history_id`),
          KEY `image_id` (`image_id`),
          KEY `user_id` (`user_id`),
          KEY `fk_tag_history_tag_id` (`tag_id`),
          CONSTRAINT `fk_tag_history_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_tag_history_tag_id` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`tag_id`) ON DELETE SET NULL ON UPDATE CASCADE,
          CONSTRAINT `fk_tag_history_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS `tag_links` (
          `tag_id` int(10) unsigned NOT NULL,
          `image_id` int(10) unsigned NOT NULL,
          `user_id` int(10) unsigned DEFAULT NULL,
          `date_linked` datetime DEFAULT current_timestamp(),
          PRIMARY KEY (`tag_id`,`image_id`),
          KEY `fk_tag_links_user_id` (`user_id`),
          KEY `fk_tag_links_image_id` (`image_id`),
          CONSTRAINT `fk_tag_links_image_id` FOREIGN KEY (`image_id`) REFERENCES `images` (`image_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_tag_links_tag_id` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`tag_id`) ON DELETE CASCADE ON UPDATE CASCADE,
          CONSTRAINT `fk_tag_links_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE SET NULL ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci
    """)

    # Re-enable FK checks
    op.execute("SET FOREIGN_KEY_CHECKS=1")


def downgrade() -> None:
    """Drop all tables in reverse order."""
    op.execute("SET FOREIGN_KEY_CHECKS=0")

    # Drop in reverse dependency order
    tables = [
        "tag_links",
        "tag_history",
        "posts",
        "image_reviews",
        "image_reports",
        "image_ratings",
        "favorites",
        "user_perms",
        "user_groups",
        "group_perms",
        "user_sessions",
        "quicklinks",
        "privmsgs",
        "news",
        "bans",
        "tags",
        "images",
        "users",
        "tips",
        "perms",
        "image_ratings_avg",
        "groups",
        "eva_theme",
        "donations",
        "banners",
    ]

    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS `{table}`")

    op.execute("SET FOREIGN_KEY_CHECKS=1")

-- One-off SQL script to rename numeric FK constraints on prod.
--
-- Background: alembic migrations 9c92a1686d79 (comment_reports) and
-- e8e9d4e6b553 (user_banner_*) originally created their ForeignKeyConstraints
-- without a `name=` argument. MariaDB assigned numeric names (`1`, `2`, `3`),
-- which are unique-per-schema in InnoDB and collide on dump restore
-- (errno 121: Duplicate key on write or update). This script renames those
-- constraints to match the explicit names now set in the migrations and
-- models.
--
-- Run once against prod after backing up:
--     mysql -u root -p shuushuu < scripts/fix_prod_fk_names.sql
--
-- NOT idempotent: a second run will fail because the numeric constraints no
-- longer exist. Verify success afterwards with:
--     SHOW CREATE TABLE comment_reports;
--     SHOW CREATE TABLE user_banner_pins;
--     SHOW CREATE TABLE user_banner_preferences;
-- Each FK should now have an `fk_<table>_<col>` name.

ALTER TABLE comment_reports
    DROP FOREIGN KEY `1`,
    DROP FOREIGN KEY `2`,
    DROP FOREIGN KEY `3`,
    ADD CONSTRAINT fk_comment_reports_comment_id
        FOREIGN KEY (comment_id) REFERENCES posts (post_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    ADD CONSTRAINT fk_comment_reports_user_id
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    ADD CONSTRAINT fk_comment_reports_reviewed_by
        FOREIGN KEY (reviewed_by) REFERENCES users (user_id)
        ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE user_banner_pins
    DROP FOREIGN KEY `1`,
    DROP FOREIGN KEY `2`,
    ADD CONSTRAINT fk_user_banner_pins_user_id
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    ADD CONSTRAINT fk_user_banner_pins_banner_id
        FOREIGN KEY (banner_id) REFERENCES banners (banner_id)
        ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE user_banner_preferences
    DROP FOREIGN KEY `1`,
    ADD CONSTRAINT fk_user_banner_prefs_user_id
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE ON UPDATE CASCADE;

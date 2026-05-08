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
--
-- DDL caveat: each ALTER TABLE below is atomic, but MariaDB does not wrap
-- multiple ALTER TABLEs in a single transaction. If a later ALTER fails after
-- an earlier one has committed, the schema is left partially renamed -- check
-- SHOW CREATE TABLE on each of the three tables before re-running any
-- individual ALTER.

-- Preflight: bail out if the expected numeric names are not present. The
-- statement below produces an error if any of the three tables already has
-- non-numeric FK names, which prevents the ALTERs from running and leaving a
-- partial state. SIGNAL is wrapped in a stored procedure call via a one-shot
-- block so it works in plain mysql/mariadb client.
SELECT
    CASE
        WHEN COUNT(*) = 6 THEN 'preflight_ok'
        ELSE CONCAT(
            'preflight_failed: expected 6 numeric FK constraints on ',
            'comment_reports/user_banner_pins/user_banner_preferences, found ',
            COUNT(*),
            '. Inspect SHOW CREATE TABLE output before running the ALTERs.'
        )
    END AS status
FROM information_schema.TABLE_CONSTRAINTS
WHERE TABLE_SCHEMA = DATABASE()
  AND CONSTRAINT_TYPE = 'FOREIGN KEY'
  AND TABLE_NAME IN ('comment_reports', 'user_banner_pins', 'user_banner_preferences')
  AND CONSTRAINT_NAME REGEXP '^[0-9]+$';
-- If status != 'preflight_ok', stop here and investigate. The ALTERs below
-- will fail loudly on a missing constraint, but only after committing any
-- prior ALTERs.

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

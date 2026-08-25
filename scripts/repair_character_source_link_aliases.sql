-- Repair character_source_links rows whose character or source side is an alias.
--
-- Links are created against canonical tags only (the API rejects aliases on
-- both sides), and aliasing a linked tag migrates its links to the canonical.
-- Rows that still point at an alias predate those guards -- legacy imports and
-- alias edits made outside the API. They duplicate the canonical tag's links
-- and are invisible to any lookup that resolves aliases first.
--
-- Re-pointing follows alias_of ONE hop, and only when the canonical can legally
-- hold the row: character side type=4, source side type=2, neither itself an
-- alias. Rows whose canonical fails that test, and rows whose canonical pair is
-- already taken, are left untouched and reported instead -- deleting either one
-- is a judgment call (it can drop a curated link picture), so it stays manual.
--
-- Nothing here deletes. Run 1 (preview) -> 2 (re-point) -> 3 (verify):
--
--   docker exec -i shuushuu-mariadb-dev mariadb -uroot -p<pw> shuushuu_dev \
--     < scripts/repair_character_source_link_aliases.sql
--
-- Statement 2 is idempotent; a second run finds nothing to do. No tag_audit_log
-- rows are written: the API's own alias-migration path (update_tag) doesn't
-- audit character-source link moves either.


-- 1. Preview. `action` says what statement 2 will do with each row.
-- The taken-pair test resolves other rows' targets without the type guard, so
-- it errs toward flagging: a row marked SKIP-taken is worth eyeballing, not
-- necessarily broken.
-- START TRANSACTION;
SELECT csl.id,
       csl.character_tag_id,
       ct.title AS character_title,
       COALESCE(cc.tag_id, csl.character_tag_id) AS target_character_tag_id,
       csl.source_tag_id,
       st.title AS source_title,
       COALESCE(cs.tag_id, csl.source_tag_id) AS target_source_tag_id,
       (SELECT COUNT(*) FROM character_source_link_pictures p WHERE p.link_id = csl.id) AS has_picture,
       CASE
           WHEN (ct.alias_of IS NOT NULL AND (cc.tag_id IS NULL OR cc.type <> 4 OR cc.alias_of IS NOT NULL))
             OR (st.alias_of IS NOT NULL AND (cs.tag_id IS NULL OR cs.type <> 2 OR cs.alias_of IS NOT NULL))
               THEN 'SKIP - canonical cannot hold this row, review by hand'
           WHEN EXISTS (
               SELECT 1
               FROM character_source_links other
               JOIN tags other_ct ON other_ct.tag_id = other.character_tag_id
               JOIN tags other_st ON other_st.tag_id = other.source_tag_id
               WHERE other.id <> csl.id
                 AND COALESCE(other_ct.alias_of, other.character_tag_id)
                     = COALESCE(cc.tag_id, csl.character_tag_id)
                 AND COALESCE(other_st.alias_of, other.source_tag_id)
                     = COALESCE(cs.tag_id, csl.source_tag_id)
           ) THEN 'SKIP - canonical pair already taken, review by hand'
           ELSE 'REPOINT'
       END AS action
FROM character_source_links csl
JOIN tags ct ON ct.tag_id = csl.character_tag_id
JOIN tags st ON st.tag_id = csl.source_tag_id
LEFT JOIN tags cc ON cc.tag_id = ct.alias_of
LEFT JOIN tags cs ON cs.tag_id = st.alias_of
WHERE ct.alias_of IS NOT NULL
   OR st.alias_of IS NOT NULL
ORDER BY csl.id;


-- 2. Re-point onto the canonical tags. `taken` holds every link's resolved
-- target pair, so a row is skipped both when another row already sits at its
-- target and when another alias row is heading for the same pair -- the update
-- can't trip unique_character_source.
UPDATE character_source_links csl
JOIN tags ct ON ct.tag_id = csl.character_tag_id
JOIN tags st ON st.tag_id = csl.source_tag_id
LEFT JOIN tags cc ON cc.tag_id = ct.alias_of
LEFT JOIN tags cs ON cs.tag_id = st.alias_of
LEFT JOIN (
    SELECT other.id,
           COALESCE(other_ct.alias_of, other.character_tag_id) AS target_character_tag_id,
           COALESCE(other_st.alias_of, other.source_tag_id) AS target_source_tag_id
    FROM character_source_links other
    JOIN tags other_ct ON other_ct.tag_id = other.character_tag_id
    JOIN tags other_st ON other_st.tag_id = other.source_tag_id
) taken
  ON taken.id <> csl.id
 AND taken.target_character_tag_id = COALESCE(cc.tag_id, csl.character_tag_id)
 AND taken.target_source_tag_id = COALESCE(cs.tag_id, csl.source_tag_id)
SET csl.character_tag_id = COALESCE(cc.tag_id, csl.character_tag_id),
    csl.source_tag_id = COALESCE(cs.tag_id, csl.source_tag_id)
WHERE (ct.alias_of IS NOT NULL OR st.alias_of IS NOT NULL)
  AND (ct.alias_of IS NULL OR (cc.tag_id IS NOT NULL AND cc.type = 4 AND cc.alias_of IS NULL))
  AND (st.alias_of IS NULL OR (cs.tag_id IS NOT NULL AND cs.type = 2 AND cs.alias_of IS NULL))
  AND taken.id IS NULL;


-- 3. Verify. `repairable_remaining` must be 0; whatever is left in
-- `alias_side_rows_remaining` is the manual-review pile, listed by statement 1.
SELECT
    SUM(CASE
            WHEN (ct.alias_of IS NULL OR (cc.tag_id IS NOT NULL AND cc.type = 4 AND cc.alias_of IS NULL))
             AND (st.alias_of IS NULL OR (cs.tag_id IS NOT NULL AND cs.type = 2 AND cs.alias_of IS NULL))
             AND NOT EXISTS (
                 SELECT 1
                 FROM character_source_links other
                 JOIN tags other_ct ON other_ct.tag_id = other.character_tag_id
                 JOIN tags other_st ON other_st.tag_id = other.source_tag_id
                 WHERE other.id <> csl.id
                   AND COALESCE(other_ct.alias_of, other.character_tag_id)
                       = COALESCE(cc.tag_id, csl.character_tag_id)
                   AND COALESCE(other_st.alias_of, other.source_tag_id)
                       = COALESCE(cs.tag_id, csl.source_tag_id)
             )
                THEN 1 ELSE 0
        END) AS repairable_remaining,
    COUNT(*) AS alias_side_rows_remaining
FROM character_source_links csl
JOIN tags ct ON ct.tag_id = csl.character_tag_id
JOIN tags st ON st.tag_id = csl.source_tag_id
LEFT JOIN tags cc ON cc.tag_id = ct.alias_of
LEFT JOIN tags cs ON cs.tag_id = st.alias_of
WHERE ct.alias_of IS NOT NULL
   OR st.alias_of IS NOT NULL;
-- ROLLBACK;

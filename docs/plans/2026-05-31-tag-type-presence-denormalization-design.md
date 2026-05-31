# Denormalized Per-Image Tag-Type Presence

**Date:** 2026-05-31

## Problem

The `missing_tag_types` image-search filter (PR #228) finds images lacking a tag of a given type (Theme/Source/Artist/Character) via a live anti-join (`image_id NOT IN (...)` / `NOT EXISTS`). On production-sized data (1.1M images, 14.7M tag_links, 956k of ~1.09M visible images already have an artist tag) this **times out (>60s)**:

- MariaDB 12 rewrites the anti-join into a **materialized semijoin**, building the ~956k "has-artist" image set every request, which defeats the primary-key-ordered early-termination that makes normal listing fast (0.4s).
- Forcing per-row execution (`optimizer_switch='semijoin=off,materialization=off'`) makes the *page* fast but the **`total` count still can't early-terminate** and times out.

Legacy (`shuu-php`) used the same anti-join but mitigated it with old-MySQL per-row execution + has-next pagination (no exact count), gated to mods. Rather than replicate those workarounds, we denormalize tag-type presence onto the image row so the filter becomes a cheap, indexed, sargable column predicate — fast page *and* exact total.

## Data model

Four boolean columns on `images` (the slow anti-join is replaced by reading these):

| Column | Meaning |
|---|---|
| `has_theme` | image has ≥1 tag of type 1 |
| `has_source` | image has ≥1 tag of type 2 |
| `has_artist` | image has ≥1 tag of type 3 |
| `has_character` | image has ≥1 tag of type 4 |

Type: `Boolean`/`TINYINT(1)`, `NOT NULL`. The model column carries `Field(default=False)` (ORM insert path); the DB-side `DEFAULT 0` safety-net lives in the migration's `ALTER` (matching the `in_r2` precedent, which keeps the server default in the migration rather than the model). These are internal fields on the `Images` table model — **not** added to `ImageBase`, so they are not exposed in public API schemas (per the project's inheritance-based security pattern).

Source of truth remains `tag_links` + `tags.type`; these columns are a maintained cache.

### Indexes

One composite index per flag, `(<flag>, image_id)`:

```
Index("idx_images_has_theme",     "has_theme",     "image_id")
Index("idx_images_has_source",    "has_source",    "image_id")
Index("idx_images_has_artist",    "has_artist",    "image_id")
Index("idx_images_has_character", "has_character", "image_id")
```

Rationale: the page query is `WHERE status IN (...) AND <flag>=0 ORDER BY image_id DESC LIMIT n`. With `(has_X, image_id)`, MariaDB ranges on the `has_X=0` equality prefix and walks `image_id` descending **already ordered**, applying `status` as a cheap residual row filter — giving early-termination (no filesort). Putting the 3-value `status IN (...)` set (`PUBLIC_IMAGE_STATUSES`) *inside* the index between `has_X` and `image_id` would break the single ordered stream, so `status` is deliberately left out of the index. The count (`WHERE status IN (...) AND has_X=0`) range-scans the `has_X=0` prefix (~133k entries for artist) and filters `status` residually — fast.

Caveat — `any` mode (`has_artist=0 OR has_source=0`): an OR across two columns can't use a single index cleanly and the matching set is large, so `ORDER BY image_id DESC LIMIT` may filesort/index-merge. The primary moderation query is single-type, which is well served; the plan must `EXPLAIN`-verify both single-type and `any`-mode page queries against real data (continuing #228's EXPLAIN discipline) and revisit if `any` mode is too slow.

Trade-off: 4 secondary indexes add write cost on image insert and tag edits. On an image board those are far rarer than reads, so this is acceptable. (Open to indexing only artist/source if write cost is a concern.)

## Filter rewrite (`missing_tag_types`)

Replace the anti-join where-block in `list_images` (`app/api/v1/images.py`). The params, `isdecimal()` parsing, and `1–4` validation are unchanged. Map each requested type ID to its column and require it to be `False`:

```python
_MISSING_TYPE_COLUMN = {
    1: Images.has_theme,
    2: Images.has_source,
    3: Images.has_artist,
    4: Images.has_character,
}
# ... after parse + validation ...
clauses = [_MISSING_TYPE_COLUMN[t] == False for t in missing_type_ids]  # noqa: E712
if missing_tag_types_mode == "all":
    query = query.where(and_(*clauses))   # missing every listed type
else:
    query = query.where(or_(*clauses))    # missing at least one listed type
```

`and_`/`or_` are already imported. The query now composes with the PK-ordered scan and the count, both fast. The observable API behavior (which images are returned, validation, 422 on bad mode) is identical to PR #228 — only the internal mechanism changes.

## Maintenance: recompute-from-source helper

Because tag-link mutations are scattered (no single chokepoint) and `tags.type` is mutable, flags are recomputed from the source of truth (idempotent, drift-proof) rather than incrementally adjusted.

New module `app/services/tag_type_flags.py`. A single set-based primitive recomputes all four flags for a set of image IDs from the source of truth; the single-image case is just a one-element set:

```python
async def refresh_images_tag_type_flags(db: AsyncSession, image_ids: Collection[int]) -> None:
    """Recompute has_theme/source/artist/character for the given images from tag_links.

    Idempotent. Does NOT commit — participates in the caller's transaction so flag
    updates are atomic with the link change.
    """
    if not image_ids:
        return
    await db.flush()  # REQUIRED: session uses autoflush=False, so pending db.add(TagLinks)
                      # rows are invisible to the SELECT below until flushed.
    # Single set-based statement over the id set:
    #   UPDATE images i
    #   LEFT JOIN (
    #     SELECT tl.image_id,
    #       MAX(t.type=1) ht, MAX(t.type=2) hs, MAX(t.type=3) ha, MAX(t.type=4) hc
    #     FROM tag_links tl JOIN tags t ON tl.tag_id = t.tag_id
    #     WHERE tl.image_id IN :ids GROUP BY tl.image_id
    #   ) agg ON agg.image_id = i.image_id
    #   SET i.has_theme=COALESCE(agg.ht,0), i.has_source=COALESCE(agg.hs,0),
    #       i.has_artist=COALESCE(agg.ha,0), i.has_character=COALESCE(agg.hc,0)
    #   WHERE i.image_id IN :ids;

async def refresh_image_tag_type_flags(db: AsyncSession, image_id: int) -> None:
    """Convenience wrapper: refresh_images_tag_type_flags(db, [image_id])."""
```

Two properties that the reviewer flagged as load-bearing:

- **Must `flush()` first.** The session is configured `autoflush=False` (`app/core/database.py`). The "add tag" paths do `db.add(TagLinks(...))` without flushing, so the recompute's SELECT would not see the just-added link and the flag would wrongly stay `0`. Flushing inside the helper fixes this for every caller (including test fixtures, which also use `db.add`).
- **Set-based, not a per-image loop.** A single `UPDATE ... JOIN` over the id set bounds the cost of tag-level operations (see `tags.type` change below) to one statement instead of N round-trips, while still being correct for the single-image case.

### Hook sites — per-image link mutations

Call `refresh_image_tag_type_flags(db, image_id)` after the link change:

| Site | File:line (approx) | Operation |
|---|---|---|
| Single add | `app/api/v1/images.py:1811` | add one tag → refresh that image |
| Single remove | `app/api/v1/images.py:1894` | remove one tag → refresh that image |
| Upload | `app/services/upload.py:152` | initial tagging → refresh after links added |
| Batch add | `app/services/batch_tag.py:109` | refresh each affected image (pass the set) |
| Batch remove | `app/services/batch_tag.py:257` | refresh each affected image (pass the set) |
| Repost merge | `app/services/repost.py:129,148` | **refresh `original_id` (mandatory — it GAINS the repost's tags via the `INSERT ... SELECT` at ~line 129 and its flags can flip 0→1) and `repost_id`.** Place the call inside `migrate_repost_data` after the link statements so both callers (`images.py:1023`, `admin.py:770`) are covered atomically. |
| Admin report-resolution | `app/api/v1/admin.py:1562,1588` | refresh affected image |

### Hook sites — tag-level operations (use the set-based recompute)

These change the type composition of *every image linked to the tag*. Gather the affected image IDs (`SELECT DISTINCT image_id FROM tag_links WHERE tag_id = :t`) and call `refresh_images_tag_type_flags(db, ids)`:

| Site | File:line (approx) | Operation & sequencing |
|---|---|---|
| Tag delete | tag delete endpoint (`tags.py:~1592`) | There is **no** ORM Tags→TagLinks relationship; link cleanup is the DB-level `ondelete="CASCADE"` FK. Sequence: capture affected `image_id`s → `db.delete(tag)` → **`db.flush()`** (so the cascade is applied within the txn) → `refresh_images_tag_type_flags(db, ids)` → commit. Without the flush the recompute still sees the tag's links and computes stale `has_X=1`. |
| Tag type change | tag update endpoint (`tags.py:~1360`, `TagUpdate.type`) | Recompute all images linked to the tag. **Single set-based UPDATE** (not a loop) — a popular artist tag has thousands of links; per-image round-trips inside one request would be pathological. Bounded by the tag's usage; acceptable as a synchronous in-transaction statement. |
| Tag merge / alias reassignment | `app/api/v1/tags.py:1467,1475` | `tag_id` reassigned via `UPDATE tag_links`. Refresh affected images. **Nearly always a flag no-op** (the API enforces alias and canonical share a `type`, so moving artist→artist keeps `has_artist=1`); kept for drift-proofing/idempotency, not load-bearing. |

This is the highest-risk part: a missed site causes silent drift. The plan must enumerate and cover every site with a test, and the backfill script (below) doubles as a reconciliation tool if drift is ever suspected.

Note: the existing `tags.usage_count` increment/decrement triggers on `tag_links` are orthogonal to these flags — no interaction.

## Backfill

The Alembic migration only **adds the columns + indexes**. It does **not** backfill values inline (a single UPDATE over 14.7M tag_links risks long locks/timeouts). DDL must use online algorithms on the 1.1M-row InnoDB table (matching the existing precedent migration `7b2101b37080`):

- Columns: `ALTER TABLE images ADD COLUMN has_* TINYINT(1) NOT NULL DEFAULT 0, ALGORITHM=INSTANT, LOCK=NONE` (metadata-only, instant). The `DEFAULT 0` safety-net lives in the migration's raw `ALTER`; the model column carries `Field(default=False)` for the ORM insert path.
- Indexes: `ALGORITHM=INPLACE, LOCK=NONE` — without this, four `CREATE INDEX` on a 1.1M-row table block writes.
- The `downgrade()` drops the four indexes then the four columns.

A separate, idempotent, resumable script `scripts/backfill_tag_type_flags.py` populates existing rows in batches by `image_id` range:

```sql
-- per batch [lo, hi):
UPDATE images i
LEFT JOIN (
  SELECT tl.image_id,
    MAX(t.type = 1) ht, MAX(t.type = 2) hs,
    MAX(t.type = 3) ha, MAX(t.type = 4) hc
  FROM tag_links tl JOIN tags t ON tl.tag_id = t.tag_id
  WHERE tl.image_id >= :lo AND tl.image_id < :hi
  GROUP BY tl.image_id
) agg ON i.image_id = agg.image_id
SET i.has_theme = COALESCE(agg.ht, 0), i.has_source = COALESCE(agg.hs, 0),
    i.has_artist = COALESCE(agg.ha, 0), i.has_character = COALESCE(agg.hc, 0)
WHERE i.image_id >= :lo AND i.image_id < :hi;
```

Batched (e.g. 10k image_ids), logs progress, can be re-run safely (idempotent). Run by ops after the migration, before the feature is relied upon. Because the columns default to 0, the filter is harmless (over-reports "missing") until the backfill completes — note this ordering in the rollout.

## Relationship to PR #228

This supersedes PR #228's query mechanism. Reused from #228: the `missing_tag_types`/`missing_tag_types_mode` params, `isdecimal()` parsing, `1–4` validation (400), `422` on bad mode, and most behavioral tests. Replaced: the anti-join where-block → the flag-based predicate above. The `isdecimal()` fixes to `tags`/`exclude_tags` from #228 are independent and stay.

## Testing (TDD)

1. **Helper unit tests** — `refresh_image_tag_type_flags` sets each flag correctly for an image with/without each tag type; `refresh_images_for_tag` updates all linked images. Real DB, no mocks.
2. **Maintenance integration tests** — for each hook site, exercise the real endpoint/service (add tag → `has_*` becomes 1; remove the last tag of a type → back to 0; remove one of two artist tags → stays 1; tag type change → flags flip; tag delete/merge → affected images updated).
3. **Filter behavior tests** — the PR #228 `TestMissingTagTypes` cases must keep passing, but their fixtures insert `TagLinks` directly via `db_session` and never set the flags, so post-migration they would break: `test_missing_single_type_*`, `test_missing_all_mode_*`, and `test_missing_any_mode_*` would FAIL (images with links but flags=0 wrongly appear as "missing"). Fix: each fixture calls `refresh_image_tag_type_flags(db, image_id)` after inserting links (works because the helper flushes internally; also exercises the real helper). The alias test still holds — the applied tag's own `type` drives the recompute. Note `test_image_with_no_tags_matches_every_type_both_modes` passes either way (default-0 == genuinely missing), so it is NOT the regression guard; the single/all/any tests are.
4. **Count test** — `total` is correct and the query returns promptly (the original motivation).
5. **Backfill script test** — on a seeded set, the script produces the same flags the helper would.

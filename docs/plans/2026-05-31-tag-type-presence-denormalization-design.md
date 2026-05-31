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

Type: `Boolean`/`TINYINT(1)`, `NOT NULL DEFAULT 0`, `server_default="0"`. These are internal fields on the `Images` table model — **not** added to `ImageBase`, so they are not exposed in public API schemas (per the project's inheritance-based security pattern).

Source of truth remains `tag_links` + `tags.type`; these columns are a maintained cache.

### Indexes

One composite index per flag to serve both the page (`... AND <flag>=0 ORDER BY image_id DESC LIMIT`) and the count:

```
Index("idx_images_has_theme",     "has_theme",     "status", "image_id")
Index("idx_images_has_source",    "has_source",    "status", "image_id")
Index("idx_images_has_artist",    "has_artist",    "status", "image_id")
Index("idx_images_has_character", "has_character", "status", "image_id")
```

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

New module `app/services/tag_type_flags.py`:

```python
async def refresh_image_tag_type_flags(db: AsyncSession, image_id: int) -> None:
    """Recompute has_theme/source/artist/character for one image from its tag_links."""
    # SELECT which types this image currently has (one indexed query on tag_links(image_id))
    # then UPDATE images SET has_* = ... WHERE image_id = :image_id
```

```python
async def refresh_images_for_tag(db: AsyncSession, tag_id: int) -> None:
    """Recompute flags for every image linked to a given tag (bounded by the tag's usage).
    Used when a tag is deleted, merged/reassigned, or its type changes."""
```

The single-image recompute is one small indexed query (`tag_links` by `image_id`, avg ~13 rows) plus one UPDATE — negligible per mutation. The helper does **not** commit; it participates in the caller's transaction so flag updates are atomic with the link change.

### Hook sites

Call `refresh_image_tag_type_flags(db, image_id)` after the link change in each per-image mutation path:

| Site | File:line (approx) | Operation |
|---|---|---|
| Single add | `app/api/v1/images.py:1811` | add one tag → refresh that image |
| Single remove | `app/api/v1/images.py:1894` | remove one tag → refresh that image |
| Upload | `app/services/upload.py:152` | initial tagging → refresh after links inserted |
| Batch add | `app/services/batch_tag.py:109` | refresh each affected image |
| Batch remove | `app/services/batch_tag.py:257` | refresh each affected image |
| Repost cleanup | `app/services/repost.py:149` | refresh affected image(s) |
| Admin report-resolution | `app/api/v1/admin.py:1562,1588` | refresh affected image |

Call `refresh_images_for_tag(db, tag_id)` (or refresh the affected image set) in each tag-level operation that changes type composition:

| Site | File:line (approx) | Operation |
|---|---|---|
| Tag delete | (tag delete endpoint / CASCADE) | links removed → refresh images that had the tag (capture image set before delete) |
| Tag merge / alias reassignment | `app/api/v1/tags.py:1467,1475` | `tag_id` reassigned → refresh affected images |
| Tag type change | tag update endpoint (`TagUpdate.type`) | refresh every image linked to the tag |

This is the highest-risk part: a missed site causes silent drift. The plan must enumerate and cover every site with a test, and the backfill script (below) doubles as a periodic reconciliation tool if drift is ever suspected.

## Backfill

The Alembic migration only **adds the columns + indexes** (column add is INSTANT/metadata-only; index builds are the heavier part of the migration but one-time). It does **not** backfill values inline (a single UPDATE over 14.7M tag_links risks long locks/timeouts).

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
3. **Filter behavior tests** — the PR #228 `TestMissingTagTypes` cases still pass, but fixtures must set flags. Update fixtures to call `refresh_image_tag_type_flags(db, image_id)` after inserting `TagLinks` (this keeps tests honest by exercising the helper). The alias test still holds: the applied tag's `type` drives the recompute.
4. **Count test** — `total` is correct and the query returns promptly (the original motivation).
5. **Backfill script test** — on a seeded set, the script produces the same flags the helper would.

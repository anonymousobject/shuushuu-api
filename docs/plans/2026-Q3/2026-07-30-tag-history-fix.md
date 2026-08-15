# Include upload-time tags in tag & user history

## Context

A tag's history page (e.g. `https://e-shuushuu.net/tags/235801/`) only shows entries for tags
added *after* an image was uploaded. Tags applied *at upload time* never appear, which makes the
history read as incomplete.

**Root cause is read-side, not write-side.** `app/services/upload.py:136` (`link_tags_to_image`)
inserts only the `tag_links` association and never a `tag_history` row, while every other path
(manual add, batch tag, ML approval, admin) writes both. But `tag_links` already carries
`date_linked` **and** `user_id`, so the who/when for upload-time tags is fully recorded — it just
isn't being read.

This exact problem was already solved once, at the image level:
`GET /images/{image_id}/tag-history` (`app/api/v1/images.py:1567`) merges `tag_links` + `tag_history`
and its docstring calls out the upload case explicitly. Two sibling endpoints never got that
treatment and still read `tag_history` alone:

- `GET /tags/{tag_id}/usage-history` — `app/api/v1/tags.py:1256`
- `GET /users/{user_id}/history` — `app/api/v1/history.py:31`

**So: no data backfill and no upload-path change.** Apply the existing merge pattern to the two
remaining endpoints.

### Production data (measured)

| metric | value |
|---|---|
| `tag_links` rows | 14,769,196 |
| `tag_history` rows | 320,485 |
| hottest tag (`tag_id=46`) links | 728,250 |
| hottest user (`user_id=15498`) links | 1,130,159 |
| `tag_audit_log` / `image_status_history` rows | 5,908 / 321 |
| `tag_links.date_linked` nulls | **0** (range 2006-08-22 → now) |
| `tag_links.user_id` nulls | 622 (0.004%) |
| `tag_history.action` values | `'a'` 298,445 / `'r'` 22,040, no nulls |

Two consequences:

1. `tag_history` covers only ~2% of tag applications — the missing history is the norm, not an edge case.
2. **The image endpoint's load-all-and-merge-in-Python approach cannot be copied.** It is justified
   by a comment noting per-image churn is tiny (5000-row cap). At 728k rows for one tag and 1.13M for
   one user, both new merges must be paginated in SQL.

`date_linked` being non-null across all 14.7M rows means it is a safe sort key with no sentinel handling.

## Approach

### 1. `GET /tags/{tag_id}/usage-history` (`app/api/v1/tags.py:1256`)

Replace the single `TagHistory` select with a SQL `UNION ALL` of two branches, both filtered to `tag_id`:

- **link branch** — `tag_links`: `tag_history_id` = `CAST(NULL AS Integer)`, `action` = literal `'a'`,
  date = `date_linked`, source lane `0`, tiebreak `image_id`.
- **history branch** — `tag_history`: real columns, source lane `1`, tiebreak `tag_history_id`.

De-dup rule, mirroring `images.py:1666`: drop `action='a'` history rows whose **image** still carries
this tag, because the link branch already represents them. Use
`or_(TagHistory.action.is_distinct_from("a"), ~exists(...))` — null-safe, and the `EXISTS` probe hits
the `tag_links` PK `(tag_id, image_id)` directly.

Carry forward the documented edge case from `images.py:1660-1665`: for an add → remove → re-add tag the
original add is dropped. Keep that comment.

**Push `ORDER BY <date> DESC, <tiebreak> DESC LIMIT :offset + :per_page` into each branch** before
unioning. MariaDB materializes a `UNION ALL` derived table, so an outer-only ORDER BY filesorts the
full merged set (728k rows for the hot tag) regardless of indexes — the composite indexes in §3 only
pay off if each branch reads its top `offset + per_page` rows in index order. This is correct because
the global top `offset + per_page` rows are contained in the per-branch top `offset + per_page`
(each branch is one source lane, and its `(date, tiebreak)` order matches the outer sort restricted
to that lane).

Then the outer `ORDER BY date DESC, source_lane DESC, tiebreak DESC` with `OFFSET/LIMIT` over the
merged set (≤ 2 × (offset + per_page) rows), matching the image endpoint's `reverse=True` tuple sort.

Compute `total` as the **sum of two plain COUNTs** (link branch + dedup-filtered history branch), not
`COUNT(*)` over a union subquery — the union form materializes a temp table and measured ~10× slower
(815 ms vs 80 ms for the hot tag; see §3 measurements).

Users can't be eager-loaded through a union — instead batch-hydrate only the current page's
`user_id`s in a second query using the existing
`selectinload(Users.user_groups).selectinload(UserGroups.group)` pattern, then build `TagHistoryResponse`
items as today.

### 2. `GET /users/{user_id}/history` (`app/api/v1/history.py:31`)

This endpoint currently loads **all** rows from three tables into memory and sorts in Python — its own
comment at `history.py:92-95` already flags that this doesn't scale. Adding `tag_links` as a fourth
in-memory source would mean loading 1.13M rows for the top user, so this needs the same SQL-side
rework rather than a fourth `db.execute`.

`UNION ALL` four branches projecting a **uniform sort/identity tuple only** — not full row data:

`(kind, id_a, id_b, ts, sort_priority, tiebreak_id)`

| kind | source | filter | sort_priority | id_a / id_b |
|---|---|---|---|---|
| 1 | `tag_audit_log` | `user_id` | 1 | `id` / 0 |
| 2 | `tag_history` | `user_id`, add-dedup **user-scoped** (below) | 2 | `tag_history_id` / 0 |
| 3 | `tag_links` (new) | `user_id` | 2 | `tag_id` / `image_id` |
| 4 | `image_status_history` | `user_id` + visible statuses | 3 | `id` / 0 |

`tag_links` needs two id columns because it has a composite PK and no surrogate id; its sort
tiebreak is `image_id`.

**The dedup here must also match on user.** The tag-endpoint rule (drop `action='a'` history when the
image currently carries the tag) over-drops in a per-user feed: if user X added a tag, it was removed,
and user Y re-added it, X's add-history row would be dropped from X's feed while the replacement link
event appears only in Y's feed — X's activity silently vanishes. A history row can only duplicate a
link *within this user's feed* if the link is theirs, so the `EXISTS` probe gains
`TagLinks.user_id == user_id`. Still a cheap PK-prefix lookup.

Preserve the existing ordering — `(timestamp, type_priority, source_id)` with `reverse=True`, i.e.
all `DESC`, so status(3) still precedes audit(1) on identical timestamps. Kind 3 sits at priority 2
alongside `tag_history` so tag-usage events group together as they do now. Because kinds 2 and 3
share priority 2 but draw tiebreaks from unrelated id spaces (`tag_history_id` vs `image_id`), add
`kind` to the sort between priority and tiebreak — mirroring the image endpoint's source-lane trick.
And because kind 3's `(ts, image_id)` is still not unique — a multi-tag upload writes N links with
one server-default `date_linked` and one `image_id`, differing only in `tag_id`, which pagination
cut points would then slice arbitrarily (duplicated/skipped rows across pages) — append `id_a` as a
final key. Full outer sort: `ORDER BY ts DESC, sort_priority DESC, kind DESC, tiebreak_id DESC,
id_a DESC`; the kind-3 branch ORDER BY is `date_linked DESC, image_id DESC, tag_id DESC` to match.
`id_a` is a no-op for kinds 1/2/4 where `id_a = tiebreak`, and the physical index
`(user_id, date_linked, image_id)` + implicit PK suffix `tag_id` provides exactly this order, so the
pushdown still avoids a filesort. For pre-existing kinds none of this changes today's ordering
(priority uniquely determined kind until now).

Apply the same per-branch pushdown as §1: each of the four branches gets
`ORDER BY <ts> DESC, <tiebreak> DESC LIMIT :offset + :per_page`; the outer sort then handles
≤ 4 × (offset + per_page) rows. Count as the sum of four per-branch COUNTs, same as §1
(measured 98 ms for the hot user vs 1.37 s through a union temp table).

Paginate the union, then hydrate only that page: one batched query per kind present, plus the existing
`linked_tags_map` batch for audit rows (now scoped to the page instead of the whole user). Reassemble
`UserHistoryItem`s in union order. Link-derived rows become `type="tag_usage"`, `action="added"`,
reusing the existing item shape — no schema change needed for this endpoint.

### 3. Migration — `alembic/` (current head `b6f974207eb7`)

Measured indexes: `tag_links` has PK `(tag_id, image_id)`, `(user_id)`, `(image_id)`; `tag_history` has
`(tag_id)`, `(user_id)`, `(image_id)`. None cover the new date-ordered scans, so both merges would
filesort — 728k rows for the hot tag.

Add composite indexes:

- `tag_links (tag_id, date_linked)` — InnoDB's implicit PK suffix appends `image_id`, so index order
  within a tag is exactly the branch's `ORDER BY date_linked, image_id`.
- `tag_links (user_id, date_linked, image_id)` — `image_id` **must be explicit** here: with
  `(user_id, date_linked)` alone the implicit PK suffix is `(tag_id, image_id)`, giving index order
  `(date_linked, tag_id, image_id)`, which mismatches the branch's `(date_linked, image_id)` tiebreak
  and leaves a 1.13M-entry filesort (measured 216 ms vs 0.7 ms). Same size either way — identical four
  columns, reordered.
- `tag_history (tag_id, date)`
- `tag_history (user_id, date)`

`tag_audit_log` and `image_status_history` are small enough (5,908 / 321 rows) to need nothing.

### Measured (dev restore at prod scale: 14.77M links; MariaDB 11.8, 2 GB buffer pool, warm runs)

| query (pushdown form, per_page=50) | without indexes | with indexes |
|---|---|---|
| tag 46 page 1 | 111 ms | 14 ms |
| tag 46 offset 10,000 | 265 ms | 26 ms |
| user 15498 page 1 | 790 ms | 0.7 ms |
| user 15498 offset 10,000 | 810 ms | 7 ms |
| tag 46 count, split form | — | 80 ms |
| user 15498 count, split form | — | 98 ms |

The naive no-pushdown union stays at 825 ms (tag) / 1.5 s (user) per request *even with* the indexes —
the pushdown is what makes them pay off. Union-subquery counts likewise stay at 815 ms / 1.37 s, hence
the split-count requirement.

Build time / size (dev hardware): `tag_links` composites 17–24 s / 282 MB and 340 MB;
`tag_history` composites 0.4 s / 7.5 MB each. ≈ 640 MB of new index total (compare: the table's
existing indexes total ~1.1 GB). Put the build-time expectation in the migration docstring.

Optional follow-ups, **not** this migration: the composites make `fk_tag_links_user_id` (285 MB),
`fk_tag_history_tag_id` (7.5 MB), and `tag_history.user_id` (5.5 MB) redundant as FK-support indexes
(each FK column is a prefix of a composite), so dropping them would offset most of the new space. The
tag endpoint's link-branch count could also read trigger-maintained `tags.usage_count` (~80 ms → ~1 ms)
at the cost of trusting the counter.
Mirror the index declarations into `TagLinks.__table_args__` (`app/models/tag_link.py:63`) and the
`TagHistory` model, respecting that file's note that Alembic is the source of truth for prod schema.

### 4. Schema — `app/schemas/audit.py`

Widen `TagHistoryResponse.tag_history_id` to `int | None = None` (resolving that file's pre-existing
TODO), move the rationale comment to the base, and delete the now-redundant override plus its
`type: ignore` from `ImageTagHistoryResponse`. Needed because the tag endpoint now emits link-derived
rows with no `tag_history` id. Safe: the only other consumer of `TagHistoryResponse` is the tag
endpoint itself. (This was previously applied only in an uncommitted working copy on the prod
server — this repo does not have it; do it as part of this work.)

## Files

- `app/api/v1/tags.py` — rewrite `get_tag_usage_history` (~1256-1330)
- `app/api/v1/history.py` — rewrite `get_user_history` query/pagination/hydration
- `app/schemas/audit.py` — widen `tag_history_id` (§4)
- `app/models/tag_link.py`, `app/models/tag_history.py` — index declarations
- `alembic/versions/` — new revision off `b6f974207eb7`
- `tests/api/v1/test_tag_usage_history.py`, `tests/api/v1/test_user_history_endpoint.py`

## Tests

Mirror the cases already proven for the image endpoint in
`tests/api/v1/test_image_tag_history_endpoint.py` — notably `test_upload_tags_appear_as_added_events`
(:107), `test_present_tag_not_duplicated_when_also_in_history` (:171), `test_removed_tag_shows_add_and_remove`
(:222).

For each of the two endpoints add: upload-time link appears as an `added` event; a tag with both a link
and an add-history row appears exactly once; a removed tag still shows both its add and remove; ordering
is correct across interleaved link and history rows; pagination is correct across the union boundary
including same-timestamp ties at the page edge (the case most likely to break under the per-branch
LIMIT pushdown); `user_id IS NULL` links (622 exist) surface with `user: null` on the tag endpoint —
on the user endpoint they can never match (`user_id = ?` is never true for NULL), so assert absence.

User endpoint only: the user-scoped dedup — user X adds a tag, it's removed, user Y re-adds it; X's
history must still show X's original add (and Y's feed shows the link event).

Existing tests in both files must keep passing unchanged — they assert current `tag_history`-only
behaviour, and any that now legitimately expect an extra link-derived event should be updated
deliberately, not blanket-adjusted.

## Verification

1. `.venv/bin/python -m pytest tests/api/v1/test_tag_usage_history.py tests/api/v1/test_user_history_endpoint.py tests/api/v1/test_image_tag_history_endpoint.py`
2. Full suite for regressions in tag/image/history endpoints.
3. Apply the migration to the pytest DB and confirm it's reversible.
4. Against prod data, `EXPLAIN` the new union for `tag_id=46` (728k links) and `user_id=15498` (1.13M links)
   to confirm each branch reads via its composite index with no per-branch filesort (branch rows show
   `Using where`/`Using index` only; ignore EXPLAIN's large row estimates — they don't account for
   LIMIT). The outer sort may still report a filesort — that's fine; it covers only the merged branch
   outputs (bounded by branches × (offset + per_page)), not the full row set. Timings should be in the
   ballpark of the §3 dev measurements.
5. Sanity-check tag 235801 (297 links / 185 history rows): its history should now show meaningfully more
   entries, with no duplicate add for any currently-linked image.

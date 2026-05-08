# IQDB hash storage for similarity search — design

## Summary

Persist the IQDB signature hash on every `Images` row at index time and use it
for the existing-image "find similar" query path. Eliminates the runtime
dependency on a local thumbnail file when answering
`GET /api/v1/images/{image_id}/similar`, so similarity search keeps working
after the imminent cutover to a server where historical images live only in
R2.

## Motivation

Today `get_similar_images` reads `STORAGE_PATH/thumbs/{filename}.webp`,
forwards the bytes to `iqdb-rs` via `POST /query`, and returns the matches.
That works only because the API server happens to share a filesystem with
the thumbnails. After the planned cutover:

- Historical images exist only in R2.
- The new server has no local thumbnails for them.
- The current code would 404 every similarity request for any pre-cutover
  image.

iqdb-rs already has every image's signature in its own sqlite (populated by
`add_to_iqdb_job` at upload time). Re-uploading the bytes from R2 just to
ask "what's similar to this?" wastes egress and latency on every request,
forever. iqdb-rs additionally exposes `GET /query?h=<signature>` — a
hash-only query path that doesn't need any bytes. The fix is to capture
that hash once at index time, store it on the row, and use it on every
subsequent similarity query.

## Non-goals

- No change to `POST /api/v1/images/check-similar` — that route already has
  bytes from the user's upload, no hash lookup needed.
- No change to upload-time iqdb indexing semantics.
- No automated R2 byte-fetch fallback. Backfilling on the old server while
  local FS is still authoritative makes that path unnecessary; if any rows
  end up with `iqdb_hash IS NULL` post-cutover they 404 with an explicit
  message and are investigated case by case.

## Design

### 1. Schema

Add a single nullable column to `images`:

```python
# app/models/image.py
iqdb_hash: str | None = Field(default=None, max_length=533)
```

```sql
ALTER TABLE images ADD COLUMN iqdb_hash VARCHAR(533) NULL;
```

`533` is exactly the length iqdb-rs's `Signature::Display` impl emits:
`"iqdb_"` (5) + three f64 avgl values as 16-hex each (48) + 120 i16
signature coefficients as 4-hex each (480). The format is rigidly
length-checked in iqdb-rs's `FromStr`, so it's safe to size precisely.
Storage: ~535 B/row × ~1.6M rows ≈ 860 MB. Default charset (utf8mb4); ASCII
hex stores at 1 byte/char in practice. Nullable because backfill is async;
`NULL` is the "not yet captured" sentinel used by the query path's fallback
during the transition window. No index — lookups are by `image_id` (PK).

### 2. Capture path

`add_to_iqdb_job` in `app/tasks/image_jobs.py` already POSTs to
`/images/{image_id}` and gets back JSON whose success body is exactly:

```json
{"post_id": 1116087, "hash": "iqdb_…533-chars…", "signature": {...}}
```

The change is to parse and persist the `hash` field after a successful
POST:

```python
# inside add_to_iqdb_job, after a 200/201 response
iqdb_hash = response.json()["hash"]
try:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(
            update(Images)
            .where(Images.image_id == image_id)
            .values(iqdb_hash=iqdb_hash)
        )
    logger.info("iqdb_job_completed", image_id=image_id, hash_captured=True)
except Exception as e:
    # Best-effort: iqdb has the entry; the hash is just an optimization.
    logger.warning("iqdb_hash_persist_failed", image_id=image_id, error=str(e))
return {"success": True}
```

Decisions baked in:

- **Best-effort write.** iqdb POST succeeded → return success. A failed DB
  UPDATE doesn't roll back iqdb. The row stays NULL and the next
  similarity request falls through to the file path until a future reindex
  retries.
- **Idempotent on retry.** Re-posting the same bytes yields the same
  signature → same hash → same UPDATE.
- **Defensive JSON parse.** A malformed response body lands in the
  existing catch-all and becomes `{"success": False, "error": ...}`
  without retrying.

The dead-code helper `app/services/iqdb.py::add_to_iqdb` (no callers in
`app/`, `tests/`, or `scripts/`) is unrelated; tagged with a docstring note
in a separate small change.

### 3. Query path

`get_similar_images` in `app/api/v1/images.py` gains a hash-first branch
with the existing file path as transition fallback:

```python
async def get_similar_images(image_id, threshold, db):
    image = (await db.execute(
        select(Images).where(Images.image_id == image_id)
    )).scalar_one_or_none()
    if not image:
        raise HTTPException(404, "Image not found")

    if image.iqdb_hash:
        similar = await check_iqdb_similarity_by_hash(
            image.iqdb_hash, threshold=threshold
        )
    else:
        # Transitional fallback for rows that pre-date iqdb_hash. Removed
        # once `populate_iqdb.py --only-missing-hash` reports zero NULLs.
        thumb_path = FilePath(settings.STORAGE_PATH) / "thumbs" / f"{image.filename}.webp"
        if not thumb_path.exists():
            raise HTTPException(404, "Image unavailable for similarity search")
        similar = await check_iqdb_similarity(thumb_path, db, threshold=threshold)

    similar = [r for r in similar if r["image_id"] != image_id]
    # ... existing hydrate logic
```

A new sibling helper in `app/services/iqdb.py` parallels the existing
`check_iqdb_similarity`:

```python
async def check_iqdb_similarity_by_hash(
    iqdb_hash: str, *, threshold: float | None = None
) -> list[dict[str, int | float]]:
    """Query IQDB for similar images using a stored signature hash.

    Uses iqdb-rs's `GET /query?h=<hash>` path which re-runs the similarity
    search against the in-memory index using the given signature, no
    bytes uploaded.
    """
    if threshold is None:
        threshold = settings.IQDB_SIMILARITY_THRESHOLD
    try:
        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/query"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(iqdb_url, params={"h": iqdb_hash})
        if response.status_code != 200:
            return []
        results = response.json()
        return [
            {"image_id": r["post_id"], "score": r["score"]}
            for r in results
            if r.get("score", 0) >= threshold
        ]
    except (httpx.RequestError, httpx.TimeoutException, ValueError, KeyError):
        return []
```

Decisions baked in:

- **New function rather than overloading the existing one.** The existing
  `check_iqdb_similarity` takes a file path and a `db` it doesn't use;
  bloating its signature with a hash-or-file branch makes the eventual
  decommission harder. With a separate sibling, removing the file path
  later is "delete one function and rename the survivor."
- **GET, not POST.** iqdb-rs accepts both for `/query`; GET is the
  read-shaped verb and matches what the URL is doing semantically.
- **No explicit `limit` parameter.** iqdb-rs defaults to 20; the existing
  function doesn't pass one either, preserving behavior.
- **Self-exclusion stays in the route.** iqdb-rs returns the query image
  itself; the existing `[r for r in similar if r["image_id"] != image_id]`
  handles both code paths.
- **`POST /api/v1/images/check-similar` is untouched.** That path has
  bytes from the user's upload — no hash to use, no fallback needed.

### 4. Backfill

The work folds into the existing `scripts/populate_iqdb.py`, which already
keyset-paginates `Images`, reads local thumbnails, and POSTs to
`/images/{image_id}`. The changes:

- **Capture the `hash` from the iqdb-rs response** in `add_image_to_iqdb`
  (renamed return shape, or thread the hash through alongside the existing
  `(success, message)` tuple).
- **`UPDATE images SET iqdb_hash = ?`** per successful post. One UPDATE per
  row — bulk would mean tracking an in-memory hash→id map and a `CASE
  WHEN`, with negligible speed gain on top of the iqdb round-trip.
- **Drop the `WHERE Images.status == 1` filter.** All images are valid
  similarity-search targets; the route doesn't filter by status either.
- **Add `--only-missing-hash`** flag that adds `Images.iqdb_hash.is_(None)`
  to the WHERE clause. Makes re-runs essentially no-ops once the column is
  populated.
- **Async fan-out per batch.** Replace the sequential `for image in batch:`
  loop with `asyncio.gather` of `--concurrency` (default 50) coroutines
  per batch. iqdb-rs is single-writer for inserts (write lock per insert),
  but the read/decode side parallelizes; this brings a 1.6M-image run
  from "tens of hours" down to "an hour or two." Tunable down if iqdb-rs
  misbehaves under load.

The script's purpose generalizes from "populate iqdb from scratch" to "make
sure iqdb is in sync with the DB and the DB has every hash" — a single
source of truth for that operation.

Idempotent: iqdb-rs's `POST /images/{id}` deletes-then-inserts when the id
already exists; same bytes yield the same signature; the UPDATE writes the
same value. Resumable: keyset cursor (`image_id > last_id`) plus the
`--only-missing-hash` filter mean a killed script re-run picks up where
it left off without bookkeeping.

Side effect worth flagging in the script's docstring: it heals broken iqdb
entries. Some rows have `iqdb_hash IS NULL` not because they predate the
column, but because their original `add_to_iqdb_job` exhausted retries —
meaning iqdb-rs has no entry for them either. The backfill repairs both.

Pre-flight: hit `GET /status` on iqdb-rs first; bail loudly if unreachable
rather than racking up retry timeouts for hours.

### 5. Testing

Three new test surfaces:

- **`test_check_iqdb_similarity_by_hash`** (unit): mock httpx, assert the
  GET URL has `?h=` and that threshold filtering matches the file-based
  sibling's behavior.
- **`test_add_to_iqdb_job_persists_hash`** (unit/integration): with a
  mocked iqdb-rs response, confirm `images.iqdb_hash` is set after a
  successful POST, and that a failed UPDATE doesn't fail the job.
- **`test_get_similar_images_uses_hash_when_available`** and
  **`test_get_similar_images_falls_back_to_thumbnail`** (API tests): two
  cases — `iqdb_hash` populated (asserts hash-based call, no file read)
  vs. `iqdb_hash IS NULL` and thumbnail present (asserts file-based
  fallback path is taken).

The backfill script doesn't get a heavy test suite — its logic is "for row:
post; capture; update," all mockable but not worth the overhead. A smoke
test of the modified `add_image_to_iqdb` returning a hash is enough.

## Error handling

| Failure | Behavior |
|---|---|
| iqdb-rs unreachable during query | empty similar list (existing behavior) |
| `iqdb_hash IS NULL` *and* local thumb missing (post-cutover edge) | 404 with explicit message |
| iqdb POST fails in `add_to_iqdb_job` | existing Retry semantics; hash captured on a successful retry |
| iqdb POST succeeds, DB UPDATE fails in capture | logged warning; row stays NULL; falls through to file path until next reindex |
| Backfill script dies mid-run | re-run; `--only-missing-hash` makes it self-resuming |
| Backfill encounters image with no local thumb | logs and skips; counted in summary; investigate before cutover |

## Rollout

1. Land this PR: schema migration + capture in `add_to_iqdb_job` +
   hash-first query in `get_similar_images` (with file fallback) + backfill
   changes in `populate_iqdb.py`.
2. Deploy. From this point every new upload's `iqdb_hash` is captured live.
3. Run `populate_iqdb.py --only-missing-hash` on the old server. Monitor
   logs; re-run on failure (resumable).
4. Verify `SELECT COUNT(*) FROM images WHERE iqdb_hash IS NULL` reaches
   zero (or matches the count of legitimately-skipped rows from the run
   summary, which are investigated before cutover).
5. Cutover snapshot taken; new server boots; similarity search works
   end-to-end on R2-only images via the hash path.
6. **Follow-up PR (out of scope for this design):** remove the file-fallback
   branch in `get_similar_images` and the now-unused `check_iqdb_similarity`
   function. Optionally trim `populate_iqdb.py` flags that no longer earn
   their keep.

## Open questions / future work

- **iqdb-rs sqlite as a dump source.** A faster backfill would read
  iqdb-rs's sqlite directly and bulk-import the hashes, skipping the
  re-POST entirely. Rejected for this design (requires understanding
  iqdb-rs's internal schema, which is fair game to change), but worth
  reconsidering if the cutover window tightens.
- **Removing dead code.** `app/services/iqdb.py::add_to_iqdb` has no
  callers; flagged with a docstring note in PR #210. Deleting it is a
  separate small change.
- **`scripts/populate_iqdb.py` flag trim.** Once the file-fallback in the
  route is removed, `--skip-missing` and `--start-from` may no longer earn
  their complexity. Decide at follow-up time.

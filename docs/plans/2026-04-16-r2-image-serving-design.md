# R2 Image Serving Design

**Date:** 2026-04-16

## Problem

Images are served from a local filesystem at `/shuushuu/images/{fullsize,thumbs,medium,large}/`. The current production server's local storage cannot grow to accommodate continued uploads, and a planned migration to a new server with less local disk requires moving image storage off the box. All 1.6TB of existing images have already been uploaded to a single Cloudflare R2 bucket named `shuushuu-images`, but no public access is configured and the application does not read from or write to R2.

This design covers serving images from R2 while keeping local filesystem storage operational in development and as a transitional fallback in production.

## Goals

- Serve public images (status ACTIVE, SPOILER, REPOST) directly from Cloudflare CDN, bypassing FastAPI on the read path.
- Serve protected images (status REVIEW, LOW_QUALITY, INAPPROPRIATE, OTHER) through FastAPI permission checks that 302-redirect to short-lived R2 presigned URLs.
- New uploads write to local filesystem AND R2 (dual-write) so local remains a working fallback during phase 1.
- Development environment continues using local-only storage with no R2 dependency.
- Rollback to local-only serving is a single flag change.

## Non-goals (deferred to phase 2)

- IQDB image-matching service reading from R2 instead of local FS.
- Removal of local filesystem image storage.
- Migration of avatars and banners to R2.

## Architecture

### Two R2 buckets

- `shuushuu-images` — existing bucket, becomes the **public** bucket. A custom domain (e.g. `cdn.e-shuushuu.net`) is attached; anyone can read via that domain (R2 has no per-object ACLs — bucket access is binary).
- `shuushuu-images-private` — new bucket, **private**. S3 API access only. Reads issued as short-lived presigned URLs.

Protected-status images cannot live in the public bucket because their keys are predictable (`{date}-{image_id}.{ext}` with sequential IDs). Moving them to a private bucket is the only safe option absent a Cloudflare Worker.

### Key layout (identical in both buckets)

```
fullsize/{date}-{image_id}.{ext}
thumbs/{date}-{image_id}.webp
medium/{date}-{image_id}.{ext}
large/{date}-{image_id}.{ext}
```

Matches the current local FS layout, so the one-time split migration requires no key rewriting.

### Access paths

Normal traffic (URLs generated from the current schema):
```
Public image (status public, r2_location=PUBLIC, R2_ENABLED=true):
    client → CDN → R2 public bucket                           (FastAPI never touched)

Protected image (status protected, r2_location=PRIVATE):
    client → nginx → FastAPI → 302 → presigned R2 private URL

Transitional / fallback (r2_location=NONE, or R2_ENABLED=false, or any mismatch):
    client → nginx → FastAPI → X-Accel-Redirect or 302        (served from wherever the object is)
```

Requests that hit the `/images/*` (or `/thumbs/*`, `/medium/*`, `/large/*`) path — bookmarks, legacy URLs, any case where the schema's direct-CDN URL wasn't used — are still served correctly: FastAPI runs the permission check and redirects to the appropriate location based on `r2_location`. The endpoint is not deprecated; it is the fallback and handles every case.

## Database schema

New column on `images`:

```
r2_location: tinyint not null default 0
```

`R2Location` IntEnum:
- `NONE = 0` — object not confirmed in R2 (in-flight upload, or R2 disabled).
- `PUBLIC = 1` — canonical copy in `shuushuu-images`.
- `PRIVATE = 2` — canonical copy in `shuushuu-images-private`.

Alembic migration adds the column with default `NONE`. A separate `r2_sync.py backfill-locations` command populates existing rows based on current status after the initial bucket split is run in production.

## URL generation (`app/schemas/image.py`)

```python
PUBLIC_STATUSES = {ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST}

def _should_use_cdn(self) -> bool:
    return (
        settings.R2_ENABLED
        and self.status in PUBLIC_STATUSES
        and self.r2_location == R2Location.PUBLIC
    )

@computed_field
@property
def url(self) -> str:
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/fullsize/{self.filename}.{self.ext}"
    return f"{settings.IMAGE_BASE_URL}/images/{self.filename}.{self.ext}"

@computed_field
@property
def thumbnail_url(self) -> str:
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/thumbs/{self.filename}.webp"
    return f"{settings.IMAGE_BASE_URL}/thumbs/{self.filename}.webp"

@computed_field
@property
def medium_url(self) -> str | None:
    if not self.medium:
        return None
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/medium/{self.filename}.{self.ext}"
    return f"{settings.IMAGE_BASE_URL}/medium/{self.filename}.{self.ext}"

@computed_field
@property
def large_url(self) -> str | None:
    if not self.large:
        return None
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/large/{self.filename}.{self.ext}"
    return f"{settings.IMAGE_BASE_URL}/large/{self.filename}.{self.ext}"
```

Direct CDN URL is emitted only when **all three** hold: `R2_ENABLED=true`, status is public, and `r2_location=PUBLIC`. Any mismatch (including `NONE` and disabled-mode) falls back to the relevant `/images/`, `/thumbs/`, `/medium/`, or `/large/` path. The `R2_ENABLED` check is essential: without it, running `backfill-locations` before flipping the flag would immediately emit CDN URLs pointing at an un-configured CDN domain.

## `/images/*` endpoint (`app/api/v1/media.py`)

```python
async def _serve_image(image, variant, db, user):
    if not await can_view_image_file(image, user, db):
        raise HTTPException(404)

    if settings.R2_ENABLED and image.r2_location == R2Location.PUBLIC:
        return RedirectResponse(
            public_cdn_url(image, variant),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
    if settings.R2_ENABLED and image.r2_location == R2Location.PRIVATE:
        presigned = r2.generate_presigned_url(
            bucket=settings.R2_PRIVATE_BUCKET,
            key=variant_key(image, variant),
            ttl=settings.R2_PRESIGN_TTL_SECONDS,
        )
        return RedirectResponse(
            presigned,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    # r2_location == NONE, or R2_ENABLED=false — serve from local FS
    return Response(headers={"X-Accel-Redirect": f"/internal/{variant}/{image.filename}.{ext}"})
```

**`Cache-Control: no-store` on 302 responses is required.** Without it, nginx or browsers may cache the redirect itself, continuing to point users at a presigned URL that has since expired (or a CDN URL whose object has since been deleted on status change). The cached image at the *target* of the redirect is still freely cacheable (CDN objects carry their own long immutable headers; presigned private-bucket responses carry no cache headers and are effectively one-shot).

Same endpoint handles both phases and both modes. Phase 2 (no local FS) simply never reaches the `NONE` branch because all uploads will have completed R2 sync before the DB row commits.

### nginx configuration

No change to existing `/images/*`, `/thumbs/*`, `/medium/*`, `/large/*` proxy blocks: they still `proxy_pass` to FastAPI. Existing `proxy_cache off` directive on these blocks remains correct — FastAPI now returns `Cache-Control: no-store` 302s, which nginx must not cache.

The `/internal/*` locations remain for the `r2_location=NONE` / `R2_ENABLED=false` fallback.

When `R2_ENABLED=true` and traffic has stabilized, the public-bucket traffic is going direct to the CDN custom domain — nginx no longer sees it at all.

## Upload flow (new images)

Existing flow unchanged:

1. `POST /images/upload` saves file to `{STORAGE_PATH}/fullsize/` locally.
2. DB row created with `r2_location=NONE`.
3. ARQ jobs enqueued for thumbnail, medium, large variants (existing `create_thumbnail_job`, `create_medium_variant_job`, `create_large_variant_job`).
4. API responds immediately.

New single orchestration job `r2_finalize_upload_job(image_id)`:

5. Enqueued immediately after step 3 with a short delay (e.g. `defer_by=60s`) so the variant jobs have time to run first.
6. On execution: reads the image row, determines which variants are expected based on the `medium`/`large` columns (which the variant jobs set). Expected set is always `{fullsize, thumbs}`, plus `medium` if `image.medium == VariantStatus.DONE`, plus `large` if `image.large == VariantStatus.DONE`.
7. For each expected variant: checks that the local file exists, then uploads it to the bucket selected by current `image.status`. If a variant file is missing (variant job failed or still pending), the finalizer logs `r2_finalize_retry` and the ARQ job retries.
8. After all expected variants are uploaded, atomically sets `r2_location` to `PUBLIC` or `PRIVATE` based on current status.

Bucket selection:
```python
def bucket_for(image: Images) -> str:
    return (
        settings.R2_PUBLIC_BUCKET
        if image.status in PUBLIC_STATUSES
        else settings.R2_PRIVATE_BUCKET
    )
```

**Why one finalizer instead of per-variant R2 upload jobs:** a single orchestration point owns the flip of `r2_location`, which is the consistency point. Per-variant jobs would race: if three jobs each try to flip `r2_location` when they finish, ordering becomes fragile. The finalizer runs once, checks everything is ready, and performs the atomic flip.

**Bucket race with status change during upload:** if status changes between upload and finalizer completion, the finalizer uploads to the bucket matching current status (re-read from DB at job start). The separate `sync_image_status_job` triggered by the status change will early-return if `r2_location` is still `NONE` — the finalizer is the authoritative writer for first-sync. If the finalizer later races with another status change, its final write might target a now-stale bucket; `r2_sync.py reconcile` and `verify` catch this.

If R2 sync fails after exhausting ARQ retries, `r2_location` stays `NONE` and the image is served from local FS via the fallback path. No user-visible impact; caught later by `r2_sync.py reconcile`.

## Status change handling

A new ARQ job `sync_image_status_job(image_id, old_status, new_status)` runs post-commit whenever `images.status` changes. Enqueued from the routes that change status (admin actions, review outcomes, repost marking, spoiler toggle).

Logic:

```
if image.r2_location == R2Location.NONE:
    raise Retry(defer=30)  # finalizer hasn't flipped r2_location yet;
                           # retry instead of returning so we don't leave
                           # the object in the wrong bucket after first-sync

# Derive desired location from current DB status (not the enqueued args,
# which may be stale if status changed again).
dst_location = location_for_status(image.status)
src_location = image.r2_location
if src_location == dst_location:
    return  # no bucket move needed

for variant in (fullsize, thumbs, medium, large):
    key = variant_key(image, variant)
    if not object_exists(src, key):
        # Idempotent replay: if object already at dst, treat as moved so
        # we still commit the DB flip rather than leaving r2_location stale.
        if object_exists(dst, key): already_at_dst.append(variant)
        continue
    copy_object(src, dst, key)
    assert object_exists(dst, key)
    delete_object(src, key)

# Conditional flip — only update if r2_location hasn't changed since we
# read it, avoiding clobbering a concurrent status transition:
update image set r2_location = dst_location where r2_location = src_location

if new_public is False:  # public → protected
    cloudflare_purge([cdn_url(v) for v in variants])
```

The flip of `r2_location` is the consistency point. Before the flip, schema emits URLs pointing to the old bucket (which still has the object). After the flip, URLs point to the new bucket. Delete-from-source runs after the flip; the CDN purge closes the exposure window for anyone who cached the old URL.

### Known cache-purge window

There is a small window (seconds to minutes) between a public→protected status change committing and Cloudflare's purge-by-URL API completing. Anyone holding the CDN URL during that window may still fetch the image from edge cache. Accepted as a phase-1 tradeoff; a future synchronous "nuke from CDN" admin action can close the window for urgent cases.

### Presigned URL lifetime on status change

Presigned URLs issued from the private bucket remain valid until their `R2_PRESIGN_TTL_SECONDS` TTL elapses, regardless of subsequent status changes. Transitioning an image between two protected statuses (e.g., REVIEW → INAPPROPRIATE) does not invalidate outstanding presigned URLs. This is acceptable: both origin and destination statuses require the same permission-check gate to receive a presigned URL in the first place. Shortening the TTL is the only lever; tightening beyond 15 min traded off against re-signing cost is not warranted in phase 1.

## Image deletion flow

When an image is hard-deleted (`AdminActionType.IMAGE_DELETE`), R2 objects must be removed too. A new ARQ job `r2_delete_image_job(image_id, r2_location, filename, ext, variants)` runs post-commit:

- Reads the image's prior `r2_location` and variant flags (passed as arguments since the row is gone).
- Deletes all four object keys (or whichever exist) from the bucket indicated by `r2_location`.
- If `r2_location=PUBLIC`, also issues a Cloudflare cache purge for the four CDN URLs — otherwise the deleted image remains cached at edges indefinitely.

Failures are logged and retried by ARQ. If retries exhaust, orphan objects accumulate in R2; `r2_sync.py verify` flags orphans (objects in R2 with no matching DB row) and a future `r2_sync.py gc-orphans` command can sweep them. Out of scope for this spec; orphans are a minor storage-cost issue, not a correctness issue.

## Storage adapter (`app/services/r2_storage.py`)

Thin wrapper over `aioboto3` (R2 is S3-compatible at its endpoint).

```python
class R2Storage:
    async def upload_file(self, bucket: str, key: str, path: Path) -> None
    async def copy_object(self, src_bucket: str, dst_bucket: str, key: str) -> None
    async def delete_object(self, bucket: str, key: str) -> None
    async def generate_presigned_url(self, bucket: str, key: str, ttl: int) -> str
    async def object_exists(self, bucket: str, key: str) -> bool
```

Single shared `aioboto3.Session` reused across the app. A `DummyR2Storage` with the same interface is swapped in when `R2_ENABLED=false` — all methods raise `RuntimeError` to catch mistakes in disabled mode; no silent no-ops.

## Cloudflare cache purge (`app/services/cloudflare.py`)

One function:

```python
async def purge_cache_by_urls(urls: list[str]) -> None
```

Calls `POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache` with `{"files": urls}`. Batches into groups of 30 URLs per call (free plan limit). Uses `CLOUDFLARE_API_TOKEN` scoped to zone cache purge.

Failures are logged at ERROR and surface via `r2_sync.py verify`. An operator can issue a manual purge via `r2_sync.py purge-cache <image_id>`.

## Dual-mode operation: `R2_ENABLED` flag

A single explicit boolean `R2_ENABLED` in `app/config.py` controls which mode the app runs in. Default `false`; dev environments leave it alone. Production sets it `true` after bucket setup and the one-time split.

### When `R2_ENABLED=false`

- Upload flow does not enqueue R2 sync jobs. `r2_location` stays `NONE`.
- URL generation never emits a CDN URL (always the `/images/*` path).
- `/images/*` endpoint serves from local via X-Accel-Redirect (the `NONE` branch).
- Status-change job is a no-op.
- Cloudflare cache purge is skipped.
- `r2_sync.py` commands refuse to run, printing an error pointing at the config flag.

No `R2_*` or `CLOUDFLARE_*` env vars required. App behaves exactly like today.

### When `R2_ENABLED=true`

Everything in this design activates. A config validator in `app/config.py` requires these R2-serving settings to be non-empty at startup; mismatch fails fast:

- `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`
- `R2_PUBLIC_BUCKET`, `R2_PRIVATE_BUCKET`
- `R2_PUBLIC_CDN_URL`
- `R2_PRESIGN_TTL_SECONDS` (default 900)

`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ZONE_ID` are optional. They are only needed for CDN cache purge operations and are not required to enable R2-backed image serving.

The DB schema is identical in both modes. A prod DB dump restored on dev works without surgery; images will just show `r2_location=NONE` and serve from wherever the dev filesystem has them (or 404 if not present).

## Operational tooling (`scripts/r2_sync.py`)

A single CLI script with subcommands, sharing the app's R2 storage adapter and DB config.

- **`split-existing`** — one-time initial migration. Queries DB for protected-status images and moves their four object keys from `shuushuu-images` to `shuushuu-images-private`. Idempotent; `--dry-run` prints what would move.
- **`backfill-locations`** — one-shot. Query: `WHERE r2_location = 0`, chunked in batches of ~1000 with a fresh read per batch so rows whose status changes during the run use the latest value. Sets `PUBLIC`/`PRIVATE` based on current status. Rows finalized by a concurrent upload already have `r2_location != 0` and are naturally skipped. Any missed rows are caught later by `reconcile`.
- **`reconcile [--stale-after <duration>]`** — heals. Finds images with `r2_location=NONE` older than the threshold, checks R2, uploads missing objects from local FS, flips the flag.
- **`image <image_id>`** — inspects and re-syncs one image for operator debugging.
- **`verify [--sample N]`** — audits (read-only). Reports discrepancies only: `PUBLIC` rows whose object is missing from the public bucket; `PRIVATE` rows whose object is missing from the private bucket; `NONE` rows whose object unexpectedly exists in either bucket; cross-bucket orphans. `r2_location=NONE` with no R2 object is a legitimate state (pending finalizer, or mode disabled, or staging-imported) and is NOT reported.
- **`purge-cache <image_id>`** — manually invokes Cloudflare purge for one image's four URLs.

All commands refuse to run when `R2_ENABLED=false`.

### Bulk-backfill kill switch: `R2_ALLOW_BULK_BACKFILL`

`backfill-locations` and `reconcile` both refuse to run when `R2_ALLOW_BULK_BACKFILL=false`. Default is `false`. Steady state: **prod sets it `true` permanently, staging leaves it `false` permanently.**

Reason: both commands walk the DB for `r2_location=NONE` rows and upload local files to R2. On staging (which has a full local copy of prod images but only a small R2 bucket), an unguarded run would upload the entire prod dataset into the staging bucket. The flag is declarative and cron-proof — a nightly `reconcile` cron inherited from prod config is a no-op on staging because the flag is off, while it still heals stuck rows in prod where the flag is on. `image <image_id>` (the single-image version) and `verify` do not touch this flag; operators can still debug individual images on staging.

## Error handling and observability

Structured log events via `get_logger`: `r2_upload_started|succeeded|failed`, `r2_status_transition_started|completed|failed`, `r2_cdn_purge_started|succeeded|failed`, `r2_presigned_url_issued` (DEBUG).

Existing ARQ retry config (`ARQ_MAX_TRIES=3`) applies to new R2 jobs. Exhausted retries log at ERROR and dead-letter via existing mechanisms — no automatic alerting in this spec; surfaces via `r2_sync.py verify` / `reconcile`.

### Failure matrix

| Failure | Effect | Recovery |
|---|---|---|
| R2 API transient 5xx on upload | ARQ retries; on exhaustion `r2_location=NONE`, served from local | `r2_sync.py reconcile` |
| R2 credentials revoked | All R2 ops fail; serving still works (local fallback) | Fix credentials, reconcile |
| Cloudflare purge API fails | Object moved but cache not purged — exposure window open | `r2_sync.py purge-cache <image_id>` |
| Copy succeeds, delete fails on status change | Object in both buckets; `r2_location` already flipped | Idempotent `reconcile` deletes orphan |
| `r2_location` mismatches reality | URL generation points to wrong place | `verify` catches; `reconcile` fixes |

App never blocks on R2 availability. R2 outage means new uploads' R2 sync fails (falls back to local) and status changes defer the bucket move (fall back to local serving via FastAPI). App stays up.

## Testing

### Unit (`tests/unit/`)

- `test_r2_storage.py` — adapter methods over `moto`-mocked S3.
- `test_image_url_generation.py` — 7 statuses × 3 `r2_location` values × 2 `R2_ENABLED` values matrix (42 cases per URL field, across `url`, `thumbnail_url`, `medium_url`, `large_url`). Asserts direct CDN URL only when all three conditions align; everything else falls back to `/images/`, `/thumbs/`, `/medium/`, or `/large/` paths. Also verifies `medium_url`/`large_url` return `None` when the variant flag is `NONE`.
- `test_cloudflare_purge.py` — mocked HTTP. Batching (30 URLs per call), error handling, auth header.
- `test_dummy_r2_storage.py` — disabled-mode adapter raises rather than silently succeeding.

### Integration (`tests/integration/`)

- `test_r2_sync_jobs.py` — moto + in-memory Redis ARQ. Upload flow end-to-end; `r2_location` transitions from `NONE` to `PUBLIC`/`PRIVATE`.
- `test_status_transition_job.py` — seed object in public bucket, change status, run job, assert copy + delete + purge. Run twice; assert idempotent.

### API (`tests/api/v1/`)

- `test_media_serving.py` — extend to cover 302 redirects for `PUBLIC` and `PRIVATE` locations. Existing `NONE` path (X-Accel-Redirect) still covered.

### Dual-mode coverage

The full suite runs with `R2_ENABLED=false` (same as today). R2-specific tests explicitly enable it via a fixture that spins up moto and patches the storage singleton. CI has two jobs: one runs the default-disabled suite, another runs the R2-enabled tests only.

## Migration plan

Ordered, independently verifiable steps:

1. **Code lands behind disabled flag (safe merge).** Alembic migration adds `r2_location` (default `NONE`). Storage adapter, sync jobs, status-change job, URL logic, `/images/*` refactor, `r2_sync.py` — all implemented but gated by `R2_ENABLED=false`. Existing behavior unchanged in dev and prod.
2. **Deploy to prod, flag off.** Confirm migration runs and no behavior changed.
3. **Cloudflare setup (operator, out-of-band).** Create `shuushuu-images-private`. Attach custom domain to `shuushuu-images`. Create zone cache-purge API token. Set all `R2_*` and `CLOUDFLARE_*` env vars in the prod environment (flag still off).
4. **One-time split.** Run `r2_sync.py split-existing --dry-run`, verify counts. Run `split-existing`. Run `verify --sample 1000`.
5. **Backfill `r2_location`.** Set `R2_ALLOW_BULK_BACKFILL=true` in prod env (stays on permanently in prod), then run `r2_sync.py backfill-locations`.
6. **Flip the flag.** `R2_ENABLED=true`; restart app and workers. New uploads dual-write. Reads for public images go to CDN. Status changes move objects and purge.
7. **Verify and observe.** `verify --sample 1000` post-cutover. Monitor logs for `r2_*_failed` events for a week. Cron `r2_sync.py reconcile` daily (prod's `R2_ALLOW_BULK_BACKFILL=true` allows it; staging's `false` makes it a no-op even if cron config is inherited).

### Rollback

Set `R2_ENABLED=false`, restart. All URLs fall back to `/images/*`, served from local. R2 objects remain (harmless). DB `r2_location` values stay (not consulted while flag off). Rollback is cheap because local FS remains source-of-truth during phase 1.

## Config reference

New settings in `app/config.py`:

| Name | Default | Required when |
|---|---|---|
| `R2_ENABLED` | `false` | — |
| `R2_ACCESS_KEY_ID` | `""` | `R2_ENABLED=true` |
| `R2_SECRET_ACCESS_KEY` | `""` | `R2_ENABLED=true` |
| `R2_ENDPOINT` | `""` | `R2_ENABLED=true` |
| `R2_PUBLIC_BUCKET` | `"shuushuu-images"` | `R2_ENABLED=true` |
| `R2_PRIVATE_BUCKET` | `"shuushuu-images-private"` | `R2_ENABLED=true` |
| `R2_PUBLIC_CDN_URL` | `""` | `R2_ENABLED=true` |
| `R2_PRESIGN_TTL_SECONDS` | `900` | — |
| `R2_ALLOW_BULK_BACKFILL` | `false` | — (set true permanently in prod; leave false on staging) |
| `CLOUDFLARE_API_TOKEN` | `""` | `R2_ENABLED=true` |
| `CLOUDFLARE_ZONE_ID` | `""` | `R2_ENABLED=true` |

The existing `STORAGE_TYPE` and `S3_*` placeholders are removed to avoid confusion with the new `R2_*` names. `.env` files in the wild that still define these will be silently ignored (`Settings.model_config` has `extra="ignore"`), so removal is a hard delete with no deprecation period needed.

## Phase 2 failure behavior and operator responsibilities

Phase 2 (local FS no longer source of truth) is out of scope for implementation here, but the phase 1 design must not close off the right failure semantics for phase 2. Two invariants guide that.

### Invariant 1: local files are only deleted after R2 confirmation

When phase 2 adds a `local_cleanup_job(image_id)`, it MUST refuse to delete any local file unless `r2_location in {PUBLIC, PRIVATE}`. If `r2_location == NONE`, the local file stays regardless of its age. This is encoded as a hard precondition in the job body, not an if-else branch.

Consequence: a broken R2 integration cannot cause data loss. The worst that can happen is local files accumulate.

### Invariant 2: sustained finalizer failures surface to operators

If R2 is completely unavailable for an extended period in phase 2:

- Uploads still succeed (write to local FS). User experience unaffected.
- Finalizer jobs exhaust retries and log `event=r2_finalize_permanently_failed` at WARNING.
- `r2_location=NONE` rows accumulate.
- Local cleanup never runs for these rows → local disk usage grows.
- Reads for recent uploads still work via the local-FS fallback path.
- Once local disk fills, new uploads start failing at the OS level.

The app cannot self-heal from total R2 outage. An operator must:
1. Be alerted that sustained failures are happening.
2. Investigate and fix the R2 issue (credentials, quota, network, Cloudflare incident).
3. Run `r2_sync.py reconcile` to catch up.
4. Once `r2_location != NONE` for all backlog, the phase 2 cleanup job will sweep local copies.

### `r2_sync.py health` command (added to operational tooling)

A read-only health subcommand designed for monitoring integration:

```
$ r2_sync.py health
unsynced_count: 3
oldest_unsynced_age_seconds: 420
local_storage_used_bytes: 1680123456789
local_storage_path: /shuushuu/images

$ r2_sync.py health --json
{"unsynced_count": 3, "oldest_unsynced_age_seconds": 420, ...}
```

`unsynced_count` is `SELECT COUNT(*) FROM images WHERE r2_location = 0`. `oldest_unsynced_age_seconds` is age of the oldest such row. `local_storage_used_bytes` shells out to `du -sb {STORAGE_PATH}` (cheap — MariaDB row count dominates).

Operators are expected to wire this output to their alerting of choice. Suggested thresholds (tune per deployment):

- WARNING if `unsynced_count > 0` and `oldest_unsynced_age_seconds > 3600` (a single image stuck for an hour).
- CRITICAL if `unsynced_count > 100` or `oldest_unsynced_age_seconds > 21600` (widespread failure).
- CRITICAL if `local_storage_used_bytes` exceeds some per-deployment ceiling (separate OS-level check recommended too).

This command is available in both phase 1 and phase 2, and respects `R2_ENABLED` — refuses to run when disabled.

## Staging environment

Staging runs with its own prod-shaped DB but keeps a **full local copy of production images** on disk. To avoid double-paying R2 storage for 1.6TB, staging uses small dedicated R2 buckets containing only images generated by staging test activity.

### Isolation

- Separate buckets: `shuushuu-images-staging` (public), `shuushuu-images-staging-private`. Configured via staging's `R2_PUBLIC_BUCKET` / `R2_PRIVATE_BUCKET` env vars. Credentials in staging's `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — ideally scoped to these buckets only so a bug can't touch prod objects.
- Separate CDN domain (e.g. `cdn-staging.e-shuushuu.net`) attached to `shuushuu-images-staging`. Set as staging's `R2_PUBLIC_CDN_URL`.
- Separate `CLOUDFLARE_ZONE_ID` for the staging zone. Purges issued from staging only affect the staging zone, never prod.

### `r2_location` strategy on staging

Staging sets `R2_ALLOW_BULK_BACKFILL=false` permanently. Both `backfill-locations` and `reconcile` refuse to run, so imported prod DB rows keep `r2_location=NONE` forever. URL generation and the `/images/*` fallback serve those rows from the local FS copy that staging has — the existing dev path, zero changes.

Only images **uploaded on staging** go through the normal finalizer flow and get `r2_location=PUBLIC` or `PRIVATE` pointing at the staging buckets. Staging exercises every R2 code path — dual-write, status transitions, cache purges, presigned URLs, deletions — using only those locally-created test images.

The status-change job's `r2_location==NONE` early-return (see Status change handling) is what makes this safe when an operator changes the status of a prod-imported image on staging: the job no-ops instead of flipping `r2_location` to a bucket that doesn't contain the object.

### Testing flows covered

- **Uploads:** new uploads on staging dual-write to local + staging R2. `r2_location` transitions `NONE → PUBLIC/PRIVATE`.
- **Status changes:** status transitions on staging-uploaded images move objects between staging public and private buckets, and purge staging CDN URLs.
- **Deletions:** `IMAGE_DELETE` on staging-uploaded images deletes from staging R2 and purges staging CDN.
- **Fallback path:** browsing any prod-imported image hits the `r2_location=NONE` branch and serves from local, identical to today.

Prod-imported images cannot be used to exercise status-change R2 moves on staging (their objects don't exist in staging R2). Any test scenario that requires that transition must use staging-uploaded images.

### Known-noisy operational commands on staging

- `r2_sync.py health` reports `unsynced_count` equal to the entire prod row count on staging, permanently tripping the suggested CRITICAL threshold. Monitoring wiring should either exclude staging or use a staging-specific counting scope (only rows after the import cutoff).
- `r2_sync.py verify` is quiet on staging because `NONE` rows with no R2 object are a legitimate state by definition (see Operational tooling). Only real bucket/DB mismatches surface.

### Phase 2 compatibility

Phase 2's `local_cleanup_job` will not touch prod-imported rows on staging because Invariant 1 (local cleanup requires `r2_location != NONE`) holds permanently for them. Staging keeps its full local image copy indefinitely by construction — no special staging handling needed in phase 2.

### Cost

A staging R2 bucket accumulates only test uploads — tens to hundreds of images. Storage cost is a rounding error. Optionally seed with a handful of curated images at setup time if ready-made test fixtures are wanted; otherwise the first staging upload populates it.

## Legacy PHP application

The `shuu-php/` directory holds the legacy PHP codebase as read-only reference (`AGENTS.md`: "DO NOT modify"). It is not part of the production serving path. No coordination required for this migration.

## Cloudflare plan assumptions

Purge-by-URL is available on all Cloudflare plans (Free through Enterprise). Rate limits differ between plans; this design assumes the free-tier limit of 1000 URL-purge requests per minute is sufficient (it is — at batch size 30, that's 30,000 URLs/minute, far above expected status-change volume). If the deployment is on a paid plan, limits are higher and nothing changes.

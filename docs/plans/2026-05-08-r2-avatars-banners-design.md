# R2 storage for avatars and banners — design

**Date:** 2026-05-08
**Status:** design
**Related:** `docs/plans/2026-04-16-r2-image-serving-design.md`

## Problem

Image fullsizes, variants, and thumbnails are stored in Cloudflare R2 (public bucket for ACTIVE/SPOILER/REPOST, private bucket for protected statuses) in production, alongside parallel local-FS copies. Avatars and banners still live on local filesystem only. The production server's local-disk capacity ceiling and a planned move to a smaller-disk host both apply equally to avatars and banners — they need to live in R2 too.

This design extends R2 storage to avatars and banners using the lightest pattern that fits each: dual-write on the avatar upload hot path, one-shot backfill plus passive URL switching for banners. Both are inherently public assets (no permission gate, no status transitions), so the design is materially simpler than the image pipeline.

## Goals

- Avatars and banners stored in `R2_PUBLIC_BUCKET` and served from `R2_PUBLIC_CDN_URL` when `R2_ENABLED=true` and a per-row tracking bit is set.
- New avatar uploads dual-write to local FS + R2; failure to upload to R2 is non-fatal (request succeeds, bit stays false, URL falls back to local).
- One-shot backfill subcommands (`avatars-backfill`, `banners-backfill`) in `scripts/r2_sync.py` for existing rows.
- Banner URL generation flips to CDN once the row's bit is set; no admin upload endpoint added.
- `R2_ENABLED=false` (dev / disabled-prod) preserves today's behavior exactly: writes go local, URLs stay on `IMAGE_BASE_URL`/`BANNER_BASE_URL`, no R2 calls issued.
- Test suite gains coverage for the new dual-write, URL switching, orphan-deletion, and backfill paths.

## Non-goals

- No admin upload endpoint for banners. Banners continue to be seeded out-of-band; the `banners-backfill` subcommand registers them after files land on disk.
- No private-bucket support. Avatars and banners are public.
- No CDN purge orchestration on routine writes. Avatars are content-addressed (MD5 filenames) so updates produce a new key; banner replacements are rare and admin-purged manually if needed.
- No removal of local-FS storage for avatars/banners. Local remains a working fallback (mirrors image phase 1).
- No reconcile/verify subcommands for avatars/banners (no public/private transitions to police; orphan delete is the only deletion path and is idempotent).
- No changes to IQDB, iqdb_feed, or other unrelated subsystems.

## Architecture

### Bucket and key layout

Single bucket: `R2_PUBLIC_BUCKET`. Keys mirror the local-FS layout so backfill is a straight upload with no key rewriting:

```
avatars/{md5}.{ext}                    # e.g. avatars/abc123…def.png
banners/{path-as-stored-in-DB}         # e.g. banners/eva/full.jpg, banners/halloween/left.png
```

CDN URLs:

```
{R2_PUBLIC_CDN_URL}/avatars/{filename}
{R2_PUBLIC_CDN_URL}/banners/{path}
```

Avatar keys are content-addressed (`users.avatar` is `{md5}.{ext}`), so re-uploading the same content overwrites with identical bytes. Two users sharing an avatar resolve to the same key, mirroring the local-FS dedup semantic.

### Per-row tracking bits

Two new boolean columns:

- `users.avatar_in_r2: bool default false` — "the file referenced by `users.avatar` exists in R2 right now."
- `banners.in_r2: bool default false` — "all of `full_image`/`left_image`/`middle_image`/`right_image` referenced by this row exist in R2."

The bits exist so URL generation is safe under partial backfill: a row with the bit `false` falls back to local-FS URLs and works correctly whether or not R2 is enabled, whether or not backfill has run.

For banners, a single bit covers the row because banner layouts are committed atomically (the validator forbids mixed full/three-part). All three parts of a three-part banner must upload before the bit flips.

### URL generation rule

Always check `settings.R2_ENABLED` first, then the per-row bit. In dev with `R2_ENABLED=false`, the bit is never consulted, so a dev pulling a prod DB dump still resolves URLs locally.

```python
def avatar_url(filename: str | None, in_r2: bool) -> str | None:
    if not filename:
        return None
    if settings.R2_ENABLED and in_r2:
        return f"{settings.R2_PUBLIC_CDN_URL}/avatars/{filename}"
    return f"{settings.IMAGE_BASE_URL}/images/avatars/{filename}"
```

The banner equivalent lives on `BannerResponse._image_url` and applies the same rule with `BANNER_BASE_URL` as the fallback.

## Database schema

### Migration: `users.avatar_in_r2`

```python
def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN avatar_in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

def downgrade() -> None:
    op.drop_column("users", "avatar_in_r2")
```

### Migration: `banners.in_r2`

```python
def upgrade() -> None:
    op.execute(
        "ALTER TABLE banners ADD COLUMN in_r2 BOOLEAN NOT NULL DEFAULT 0, "
        "ALGORITHM=INSTANT, LOCK=NONE"
    )

def downgrade() -> None:
    op.drop_column("banners", "in_r2")
```

Both columns default false on existing rows; no data migration required. No indexes — these columns are read alongside the row, never queried in isolation.

**Locking:** the project runs MariaDB 12 (`docker-compose.yml`). Adding a `NOT NULL DEFAULT 0` column at the end of an InnoDB table with `ALGORITHM=INSTANT, LOCK=NONE` is metadata-only — no table rewrite, no row locks. A raw `op.execute` is used instead of `op.add_column` to make the algorithm/lock hints explicit and visible in code review; alembic's `add_column` does not surface them by default. The migration is expected to complete in tens of milliseconds even on the production `users` table.

### Model updates

- `app/models/user.py` (`UserBase` or `Users` — wherever `avatar` is declared): add `avatar_in_r2: bool = Field(default=False)`.
- `app/models/misc.py` (`BannerBase`): add `in_r2: bool = Field(default=False)`.

## Configuration

One new setting in `app/config.py`, mirroring `AVATAR_STORAGE_PATH`:

```python
BANNER_STORAGE_PATH: str = ""  # Derived from STORAGE_PATH if not set

@model_validator(mode="after")
def set_default_banner_storage_path(self) -> Settings:
    if not self.BANNER_STORAGE_PATH:
        self.BANNER_STORAGE_PATH = f"{self.STORAGE_PATH}/banners"
    return self
```

This is the on-disk path the `banners-backfill` subcommand reads from. Today there is no setting — the URL is derived from `IMAGE_BASE_URL`, but the disk path is implicit. Backfill needs an explicit knob.

The `set_default_banner_storage_path` validator is a **separate** `@model_validator(mode="after")` on `Settings`, sitting alongside (not merged into) the existing `set_default_avatar_storage_path` validator at `app/config.py:244–248`. Pydantic chains all `model_validator(mode="after")` instances and runs them in declaration order; keeping them as siblings preserves the existing pattern.

No new R2 settings — `R2_PUBLIC_BUCKET`, `R2_PUBLIC_CDN_URL`, and the existing R2 credentials are reused.

## Storage adapter changes

`app/services/r2_storage.py` gains an `upload_bytes` method on both `R2Storage` and `DummyR2Storage`:

```python
# R2Storage
async def upload_bytes(
    self, bucket: str, key: str, body: bytes, content_type: str
) -> None:
    """Upload an in-memory bytes payload with an explicit Content-Type."""
    async with self._acquire_client() as s3:
        await s3.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType=content_type
        )

# DummyR2Storage
async def upload_bytes(
    self, bucket: str, key: str, body: bytes, content_type: str
) -> None:
    raise RuntimeError(self._ERR)
```

`content_type` is **mandatory**, not optional. R2 stores whatever Content-Type is set on PUT; without one, R2 returns `application/octet-stream`, which Cloudflare's CDN passes through. `application/octet-stream` triggers download instead of inline render in some browser/CSP combinations and breaks `<img src>` under strict `X-Content-Type-Options: nosniff`. The avatar caller derives Content-Type from the extension via a small helper (`app/services/avatar.py::_avatar_content_type(ext: str) -> str` mapping `jpg/png/gif` → `image/jpeg|image/png|image/gif`).

The avatar write path uses `upload_bytes` directly because `resize_avatar` returns the processed bytes; round-tripping through a temp file just to call `upload_file` would be wasteful.

**Backfill also uses `upload_bytes`, not `upload_file`.** Boto3/aioboto3's `upload_file` does **not** auto-derive Content-Type — the underlying s3transfer library uploads with whatever `ExtraArgs={"ContentType": ...}` is passed, defaulting to no Content-Type (R2 stores `application/octet-stream`). Since `upload_file`'s only advantage over `upload_bytes` is multipart-streaming for large files, and avatars (≤1MB) and banner parts (typically <1MB each) are well below the multipart threshold, backfill reads each file into memory with `path.read_bytes()` and calls `upload_bytes(bucket, key, body, content_type)` with a Content-Type derived from the extension via `mimetypes.guess_type(path)[0]` (with a fallback for unknown extensions). Using `upload_bytes` for both the live write path and backfill keeps a single Content-Type-correct code path.

Note for future image backfill consistency: the existing `R2Storage.upload_file` is still used by `r2_sync.py split-existing` for image variants. Those uploads currently land without an explicit Content-Type — out of scope here, but flagged in case a follow-up wants to fix image Content-Type alongside.

### Failure surface for `upload_bytes`

The avatar handler catches `Exception` from the `upload_bytes` call and treats it as an R2-upload failure (log + continue with `avatar_in_r2=False`). The broad catch is intentional and matches the log-and-continue contract:

- `botocore.exceptions.ClientError` — R2 5xx, auth failures, invalid bucket
- `botocore.exceptions.EndpointConnectionError` / `ConnectionError` — network or DNS
- `asyncio.TimeoutError` — `read_timeout` or `connect_timeout` from the existing `_R2_CLIENT_CONFIG`
- `aiobotocore.session`-internal `BotoCoreError` subclasses

Errors are logged with `error=type(e).__name__` and `error_msg=str(e)` to keep the log structure useful without committing to a static taxonomy of failure types.

## Avatar write path

In `app/api/v1/users.py`, the upload-avatar handler currently calls `save_avatar(processed_content, ext)` then commits `user.avatar = new_filename`. The flow becomes:

1. Capture `old_avatar = user.avatar` and `old_in_r2 = user.avatar_in_r2` **before** any mutation.
2. Validate, resize, write to local FS via `save_avatar` → returns `new_filename`.
3. If `settings.R2_ENABLED`:
   - Attempt `r2.upload_bytes(R2_PUBLIC_BUCKET, f"avatars/{new_filename}", processed_content, content_type=_avatar_content_type(ext))`.
   - On success: `new_in_r2 = True`, log `avatar_r2_uploaded`.
   - On `Exception`: `new_in_r2 = False`, log `avatar_r2_upload_failed` with `error=type(e).__name__` and `error_msg=str(e)`. Do **not** raise — request continues.
   - Else (`R2_ENABLED=false`): `new_in_r2 = False`.
4. Set `user.avatar = new_filename`, `user.avatar_in_r2 = new_in_r2`, commit.
5. Old-avatar cleanup: call `delete_avatar_if_orphaned(old_avatar, old_in_r2, db)`. The function (a) re-counts users referencing `old_avatar` (after commit, so the current user has already moved off), (b) if zero, unlinks the local file as today, (c) **if zero AND `old_in_r2 AND R2_ENABLED`**, also calls `r2.delete_object(R2_PUBLIC_BUCKET, f"avatars/{old_avatar}")`. Idempotent; missing keys are success.

`delete_avatar_if_orphaned` gains the `old_in_r2: bool` argument so the R2 delete fires only when the file was known to exist in R2 — avoiding a needless API call for legacy local-only rows.

The `_delete_avatar` helper (`/me/avatar` and `/{user_id}/avatar` DELETE endpoints) follows the same orphan-cleanup path: capture `old_in_r2 = user.avatar_in_r2` before clearing, then call `delete_avatar_if_orphaned(old_avatar, old_in_r2, db)` after commit.

### Failure semantics

The choice to log-and-continue on R2 upload failure is deliberate (see Goals). It mirrors the image phase-1 dual-write contract: local FS is the source of truth; R2 is best-effort during the dual-write phase. A row with `avatar_in_r2=false` after a failure is not "broken" — its URL falls back to `IMAGE_BASE_URL` and serves correctly.

**No async retry queue.** This design deliberately diverges from the image pipeline, which uses `r2_finalize_upload_job` (arq) to retry uploads on failure. Avatars are different in three ways that justify the divergence:
- Avatar uploads are infrequent and small (≤200px after resize, MAX_AVATAR_SIZE=1MB pre-resize); the cost of a missed-and-not-retried upload is one user serving from local until the next backfill run.
- There is no equivalent of variant-generation latency — `resize_avatar` produces final bytes synchronously inside the request, so the `r2_finalize_upload_job`-style "wait until variants are ready" complexity does not apply.
- The orphan-delete path is the only deletion path; no public/private status transitions to track. A queued retry would also need a queued-retry equivalent for the orphan delete to preserve consistency, doubling the surface area for negligible benefit.

The trade-off: a transient R2 outage during the rollout window leaves some rows with `avatar_in_r2=false` even though their files are eventually uploadable. **The `avatars-backfill` subcommand catches up such rows.** Operationally this means: re-run `avatars-backfill` periodically (or on demand after a known R2 outage) to reach steady-state.

### Concurrency notes

- **Orphan-delete race.** Two concurrent requests can each see "0 users reference `old_avatar`" and both attempt to delete. Local `unlink` is idempotent (`exists()` check before unlink); R2 `delete_object` is idempotent (S3 treats missing keys as success). Net effect: redundant work, no incorrect state. No row lock added — the existing race is benign and the extension preserves that.
- **Same-MD5 concurrent uploads.** Two users uploading identical bytes concurrently produce the same key (`avatars/{md5}.{ext}`). Both `put_object` calls succeed (last-write-wins on identical bytes); both `avatar_in_r2` flags settle to `true`. If one upload fails transiently and the other succeeds, the failed user's flag stays `false` and their URL falls back to local — even though the R2 object exists. Acceptable: backfill catches up.
- **Re-upload of identical content.** A user uploading the same bytes they already have (`new_filename == old_avatar`) is a degenerate case. Step 5's orphan check counts users referencing `old_avatar` *after* `user.avatar = new_filename` is committed — the user themselves still references it, so count ≥ 1, and no delete fires. Verified by an explicit test (see Testing § "same-MD5 re-upload").

## Banner write path

Banners have no application-level write endpoint. Files are seeded out-of-band; rows are inserted via `scripts/seed_banners.sql` or similar. The write-path change for banners is therefore confined to:

- `BannerResponse` reads `in_r2` from the row and feeds it to `_image_url`.
- The `banners-backfill` subcommand uploads files and flips bits on existing rows.
- Future-added banners follow the same pattern: place files on disk, run `banners-backfill`.

## URL generation — call sites

### Avatars

Centralize on a single helper `app/services/avatar.py::avatar_url(filename, in_r2)`.

Update sites:

- `app/schemas/user.py` — `avatar_url` computed_field (around line 155–161) reads `self.avatar` and `self.avatar_in_r2`, calls helper.
- `app/schemas/common.py` — same shape (around line 27–33); if it's a separate base, update both.
- `app/api/v1/privmsgs.py` — **seven** inline `f"{settings.IMAGE_BASE_URL}/images/avatars/{...}"` sites at lines **273, 415, 417, 577, 667, 741, 743**. Each currently builds the URL inline from `IMAGE_BASE_URL` and a fetched `avatar` filename. Each query that fetches `avatar` must additionally fetch `avatar_in_r2` from the same `users` row (the queries already select from `users`; one extra column), then pass both into the helper. Implementation will do a final pre-merge sweep over `app/api/v1/privmsgs.py` to confirm no site is missed; this is the largest concentration of inline URL building in the codebase.

### Banners

Single update to `BannerResponse._image_url` in `app/schemas/banner.py:30`. `BannerResponse` adds `in_r2: bool` so it deserializes from the row via `model_config["from_attributes"]`. All four `*_image_url` computed fields use the updated helper.

Cache implications: `app/services/banner.py:89` deserializes cached banners via `BannerResponse.model_validate_json(cached_str)`. The cache key (`banner:current:{theme}:{size}`) does not include `R2_ENABLED` or `in_r2`, so the cached payload contains URLs computed at cache-write time. After the bit flips on a banner row, cached entries serve stale URLs until TTL expiry. With current settings — `BANNER_CACHE_TTL=600s`, `BANNER_CACHE_TTL_JITTER=300s` (`app/config.py:183–184`) — staleness is bounded at 600–900s (10–15 min). Acceptable: stale URLs still resolve through the local-FS fallback, and TTL expiry resolves the drift naturally. No deliberate cache invalidation needed.

## Backfill / ops tooling

Two new subcommands in `scripts/r2_sync.py`, both gated by the existing `require_bulk_backfill()` (R2_ENABLED + R2_ALLOW_BULK_BACKFILL).

### `avatars-backfill`

```
uv run python scripts/r2_sync.py avatars-backfill [--dry-run] [--concurrency N]
```

- Walks `users` where `avatar != ''` AND `avatar_in_r2 = false`.
- For each user (concurrent up to N):
  - Read local file bytes from `{AVATAR_STORAGE_PATH}/{filename}`. If missing, log `avatar_local_missing` and skip (bit stays false).
  - `head_object` for `avatars/{filename}` in `R2_PUBLIC_BUCKET`. If exists, skip upload (idempotent — content-addressed key).
  - Else `upload_bytes(bucket, key, body, content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")`. (See Storage adapter changes for why bytes-based upload over `upload_file`.)
  - On success, `UPDATE users SET avatar_in_r2 = true WHERE user_id = ?`.
- Wraps the loop in `r2.bulk_session()` to share one client across the burst.
- `--dry-run` reports counts without writing.
- Default `--concurrency 8` (matches `split-existing`).

### `banners-backfill`

```
uv run python scripts/r2_sync.py banners-backfill [--dry-run]
```

- Walks `banners` where `in_r2 = false`.
- For each banner:
  - Collect non-null paths from `full_image`, `left_image`, `middle_image`, `right_image` (1 path for full-image banners, 3 for three-part).
  - For each path: read bytes from `{BANNER_STORAGE_PATH}/{path}`. If any is missing, log `banner_local_missing` with the row's `banner_id` and skip the row entirely (bit stays false).
  - For each path: `head_object` then `upload_bytes(bucket, key, body, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")` if missing.
  - If **all** parts uploaded (or already existed), `UPDATE banners SET in_r2 = true WHERE banner_id = ?`. Otherwise the bit stays false; re-running picks up where it left off.
- Sequential row processing is fine — banners are tens of rows, not millions.

### Re-run safety

Both subcommands are idempotent against partial state: head-then-upload skips work already done, and the bit only flips when all uploads in a logical unit succeed. A failed run (network blip, `KeyboardInterrupt`) leaves the DB consistent — every row with `in_r2=true` has all its files in R2.

## Observability

New structured-log events, mirroring the `r2_*` event-name convention from `app/tasks/r2_jobs.py`:

- `avatar_r2_uploaded` (request path) — `user_id`, `key`
- `avatar_r2_upload_failed` (request path) — `user_id`, `key`, `error`
- `avatar_r2_orphan_deleted` (request path) — `key`
- `avatar_r2_backfilled` (script) — `user_id`, `key`, `skipped_existing`
- `avatar_local_missing` (script) — `user_id`, `filename`
- `banner_r2_backfilled` (script) — `banner_id`, `parts_uploaded`, `parts_skipped`
- `banner_r2_partial` (script) — `banner_id`, `missing` paths
- `banner_local_missing` (script) — `banner_id`, `path`

## Testing

TDD per project rules (`AGENTS.md`). New tests:

### `tests/unit/test_r2_storage.py` (extend)

- `upload_bytes` round-trip via the existing `moto_server`/`storage` fixtures. Verify object content matches input and `ContentType` is set when supplied.

### `tests/unit/test_r2_client.py` (extend)

- `DummyR2Storage.upload_bytes` raises `RuntimeError` like its peers.

### `tests/services/test_avatar.py` (new or extend)

- Upload with `R2_ENABLED=false` writes local only, sets `avatar_in_r2 = false`. (Already today's behavior modulo the new field.)
- Upload with `R2_ENABLED=true` and moto bucket: writes local AND R2, sets `avatar_in_r2 = true`. R2 object has the expected Content-Type (`image/png`/`jpeg`/`gif`).
- Upload with `R2_ENABLED=true` but R2 unavailable (patched `upload_bytes` raising): local write succeeds, `avatar_in_r2 = false`, request returns success, `avatar_r2_upload_failed` log captured.
- Orphan deletion with old `avatar_in_r2 = true`: local file unlinked AND R2 object deleted. With old `avatar_in_r2 = false`: R2 not touched.
- Orphan deletion when another user still references the avatar: neither local nor R2 touched.
- **Same-MD5 re-upload** (`new_filename == old_avatar`): the new-bytes-equal-old-bytes case. Verify the orphan check sees the user themselves still referencing the file post-commit, and neither the local nor R2 file is deleted.

### Test scope notes

The orphan-delete concurrency race (two requests racing to delete the last reference) is not covered by an automated test. The race is benign — both `unlink` and `delete_object` are idempotent — and reproducing it deterministically would require artificial sleeps or shared-state hacks that don't pay for themselves. Documented here so reviewers understand the gap is intentional.

### `tests/api/v1/test_users_avatar.py` (extend)

- Avatar URL on `UserResponse` switches to CDN URL when `R2_ENABLED=true` and `avatar_in_r2=true`; stays local otherwise.
- Privmsg responses include avatar URLs that respect the same rule (smoke-test a few of the seven call sites).

### `tests/api/v1/test_banners.py` (extend)

- `BannerResponse._image_url` returns CDN URL when `R2_ENABLED=true` and `in_r2=true`; falls back to `BANNER_BASE_URL` otherwise.
- Cover both full-image and three-part banner shapes.

### `tests/scripts/test_r2_sync_avatars.py` (new)

- Backfill happy path: rows with `avatar_in_r2=false` and local files present are uploaded and flipped to true.
- Idempotent re-run: rows already in R2 are detected via `head_object`, no re-upload, bit flipped to true.
- Missing local file: log captured, bit stays false, no R2 call.
- `--dry-run` does not write to R2 or DB.
- Refuses to run with `R2_ALLOW_BULK_BACKFILL=false`.

### `tests/scripts/test_r2_sync_banners.py` (new)

- Backfill happy path for full-image banner: single key uploaded, bit flipped.
- Backfill happy path for three-part banner: three keys uploaded, bit flipped.
- Partial-success three-part: one part missing on disk, no upload happens for that row, bit stays false, log captured.
- Idempotent re-run with `head_object` short-circuit.

All R2-touching tests reuse the `ThreadedMotoServer` fixture pattern from `tests/unit/test_r2_storage.py`; flag toggling uses `monkeypatch.setattr(settings, "R2_ENABLED", True)` per existing convention in `tests/unit/test_r2_finalize_job.py`.

## Rollout

The feature ships as a single deploy. Schema migrations, model bits, write-path dual-write, URL helpers, and backfill subcommands all land together. The order of operations *after* deploy matters more than splitting the deploy itself.

1. **Deploy.** Schema migrations apply (instant ADD COLUMN, see Database schema). All existing rows get `avatar_in_r2=false` / `in_r2=false` defaults. URL generation respects `R2_ENABLED AND row_bit` — every existing row continues to serve from local FS exactly as before. New avatar uploads start dual-writing immediately; rows uploaded after deploy will have `avatar_in_r2=true` (or `false` on R2 failure). New banners do not exist yet (no admin endpoint).
2. **Backfill avatars.** Run `uv run python scripts/r2_sync.py avatars-backfill --concurrency 8` in production (requires `R2_ENABLED=true` and `R2_ALLOW_BULK_BACKFILL=true`, both already true in prod). Walks all `users.avatar_in_r2=false` rows and uploads. Idempotent against in-flight new uploads — content-addressed keys plus `head_object` short-circuit make racing the live write path safe.
3. **Backfill banners.** Run `uv run python scripts/r2_sync.py banners-backfill`. Tens of rows, completes in seconds.
4. **Verify.** Spot-check via `curl -I` that randomly-selected avatar and banner CDN URLs return 200 with the expected `Content-Type`. Confirm a few user-facing pages render avatars and banners correctly under the new URLs.
5. **Steady state.** New uploads dual-write automatically. New banners require running `banners-backfill` after seeding files; this is documented in the script's `--help`. After a known R2 outage, re-run `avatars-backfill` to mop up rows that fell back to `avatar_in_r2=false`.

**Why one-deploy is safe:** the bit defaults `false`, the URL helper is `R2_ENABLED AND bit`, and the dual-write code path correctly handles both pre-backfill (no R2 object exists) and post-backfill (R2 object exists) starting states. There is no consistency window where a URL points at a missing R2 object.

**Window of partial-state operation** (between step 1 and step 2): some rows have `avatar_in_r2=true` (newly uploaded post-deploy), others `false` (pre-deploy rows not yet backfilled). Both serve correctly through their respective URL paths. Backfill should follow deploy promptly to minimize the period where the CDN is under-utilized, but there is no correctness deadline.

6. **(Future, out of scope)** Once stable and IQDB/avatar/banner code paths can fully read from R2, demote local FS for these classes to read-only fallback or remove entirely.

## Risks and mitigations

- **R2 upload failure on the avatar hot path** — mitigated by log-and-continue semantics. Worst case: a small number of rows have `avatar_in_r2=false` and serve from local; backfill catches up. **Important:** failed uploads are *not* retried automatically (no async finalize-job equivalent — see Avatar write path § "No async retry queue"). Operationally this means re-running `avatars-backfill` after a known R2 outage.
- **Banner cache staleness after bit flip** — accepted; bounded at 600–900s (`BANNER_CACHE_TTL=600` + `BANNER_CACHE_TTL_JITTER=300`), and the fall-back URL remains valid through the staleness window.
- **Partial backfill leaves rows pointing at local** — by design. The bit guarantees URL correctness regardless of backfill state. Re-running the backfill is safe.
- **Forgotten privmsg call site** — seven string-built URLs in `app/api/v1/privmsgs.py` (lines 273, 415, 417, 577, 667, 741, 743) are the largest concentration of inline URL building. Mitigated by the centralized helper plus tests for representative endpoints; a final `grep` over `app/api/v1/privmsgs.py` for `IMAGE_BASE_URL.*avatars` before merge confirms all sites are migrated.
- **Local-FS path for banners not configured today** — adding `BANNER_STORAGE_PATH` with a derived default keeps existing deployments working without a config change.
- **Concurrent orphan-delete or same-MD5 upload races** — analyzed in Avatar write path § "Concurrency notes". Both local and R2 deletes are idempotent; same-MD5 PUT is last-write-wins on identical bytes. No row locks added.

## Open questions

- **Helper module placement.** `avatar_url(filename, in_r2)` could live in `app/services/avatar.py` (alongside save/delete) or in `app/schemas/_helpers.py` (alongside the schemas that use it). Defaulting to `app/services/avatar.py` keeps avatar concerns co-located, but if circular-import issues arise (schemas → services), the helper moves to a leaf-level module with no other imports. Implementation will resolve.
- **Log field naming.** The R2 jobs use `image_id`, `bucket`, `key` as standard fields. Avatar/banner equivalents (`user_id` for avatar, `banner_id` for banner) are intuitive, but the exact `event=` names (`avatar_r2_uploaded` vs `r2_avatar_uploaded`, etc.) should be confirmed against `app/tasks/r2_jobs.py` conventions. Listed in Observability — minor stylistic alignment.
- **Should backfill subcommands also live in `r2_sync.py` or in a sibling script?** Defaulting to `r2_sync.py` for operational discoverability (one CLI for all R2 ops) and to reuse `require_bulk_backfill()`. The script is currently 877 lines; if these additions push it past a comfortable size, splitting into `scripts/r2/{images,avatars,banners}.py` becomes worth considering. Out of scope for this design.

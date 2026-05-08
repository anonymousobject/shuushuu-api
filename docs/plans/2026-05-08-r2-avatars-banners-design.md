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
    op.add_column(
        "users",
        sa.Column(
            "avatar_in_r2",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

def downgrade() -> None:
    op.drop_column("users", "avatar_in_r2")
```

### Migration: `banners.in_r2`

```python
def upgrade() -> None:
    op.add_column(
        "banners",
        sa.Column(
            "in_r2",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

def downgrade() -> None:
    op.drop_column("banners", "in_r2")
```

Both columns default false on existing rows; no data migration required. No indexes — these columns are read alongside the row, never queried in isolation.

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

No new R2 settings — `R2_PUBLIC_BUCKET`, `R2_PUBLIC_CDN_URL`, and the existing R2 credentials are reused.

## Storage adapter changes

`app/services/r2_storage.py` gains an `upload_bytes` method on both `R2Storage` and `DummyR2Storage`:

```python
async def upload_bytes(
    self, bucket: str, key: str, body: bytes, content_type: str | None = None
) -> None:
    """Upload an in-memory bytes payload."""
    async with self._acquire_client() as s3:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": body}
        if content_type:
            kwargs["ContentType"] = content_type
        await s3.put_object(**kwargs)
```

`DummyR2Storage.upload_bytes` raises the same `RuntimeError` its peers do, so a code path reaching it under `R2_ENABLED=false` surfaces loudly.

The avatar write path uses `upload_bytes` directly because `resize_avatar` returns the processed bytes; round-tripping through a temp file just to call `upload_file` would be wasteful. Backfill subcommands use the existing `upload_file(path)`.

## Avatar write path

In `app/api/v1/users.py`, the upload-avatar handler currently calls `save_avatar(processed_content, ext)` then commits `user.avatar = new_filename`. The flow becomes:

1. Validate, resize, write to local FS via `save_avatar` → returns `new_filename`.
2. If `settings.R2_ENABLED`:
   - Attempt `r2.upload_bytes(R2_PUBLIC_BUCKET, f"avatars/{new_filename}", processed_content, content_type=...)`.
   - On success: `new_in_r2 = True`, log `avatar_r2_uploaded`.
   - On exception: `new_in_r2 = False`, log `avatar_r2_upload_failed` with the error. Do **not** raise — request continues.
   - Else (`R2_ENABLED=false`): `new_in_r2 = False`.
3. Set `user.avatar = new_filename`, `user.avatar_in_r2 = new_in_r2`, commit.
4. Old-avatar cleanup: existing `delete_avatar_if_orphaned(old_avatar, db)` runs as today, returning whether it deleted the local file. Extension: if it deleted locally **and** the old `avatar_in_r2` was true at the start of the request **and** `R2_ENABLED`, also call `r2.delete_object(R2_PUBLIC_BUCKET, f"avatars/{old_avatar}")`. Idempotent; missing keys are success.

`delete_avatar_if_orphaned` evolves to take responsibility for the R2 delete since the orphan check (count of users referencing the file) is the only place we know it's safe. Its signature gains an `old_in_r2: bool` argument.

The `_delete_avatar` helper (`/me/avatar` and `/{user_id}/avatar` DELETE endpoints) follows the same orphan-cleanup path with `old_in_r2 = user.avatar_in_r2`.

### Failure semantics

The choice to log-and-continue on R2 upload failure is deliberate (see Goals). It mirrors the image phase-1 dual-write contract: local FS is the source of truth; R2 is best-effort during the dual-write phase. A row with `avatar_in_r2=false` after a failure is not "broken" — its URL falls back to `IMAGE_BASE_URL` and serves correctly. The `avatars-backfill` subcommand catches up such rows on a later run.

## Banner write path

Banners have no application-level write endpoint. Files are seeded out-of-band; rows are inserted via `scripts/seed_banners.sql` or similar. The write-path change for banners is therefore confined to:

- `BannerResponse` reads `in_r2` from the row and feeds it to `_image_url`.
- The `banners-backfill` subcommand uploads files and flips bits on existing rows.
- Future-added banners follow the same pattern: place files on disk, run `banners-backfill`.

## URL generation — call sites

### Avatars

Centralize on a single helper `app/services/avatar.py::avatar_url(filename, in_r2)`.

Update sites:

- `app/schemas/user.py:157` — `avatar_url` computed_field reads `self.avatar` and `self.avatar_in_r2`, calls helper.
- `app/schemas/common.py:29` — same shape; if it's a separate base, update both.
- `app/api/v1/privmsgs.py` — six string-concatenation sites at lines 271–273, 412–417, 565–577, 660–667, 738–743, 757–760. Each currently builds the URL inline from `IMAGE_BASE_URL` and a fetched `avatar` filename. Each query that fetches `avatar` must additionally fetch `avatar_in_r2` from the same `users` row, then pass both into the helper.

### Banners

Single update to `BannerResponse._image_url` in `app/schemas/banner.py:30`. `BannerResponse` adds `in_r2: bool` so it deserializes from the row via `model_config["from_attributes"]`. All four `*_image_url` computed fields use the updated helper.

Cache implications: `app/services/banner.py:89` deserializes cached banners via `BannerResponse.model_validate_json(cached_str)`. The cache key (`banner:current:{theme}:{size}`) does not include `R2_ENABLED` or `in_r2`, so the cached payload contains URLs computed at cache-write time. After the bit flips on a banner row, cached entries serve stale URLs until TTL expiry (`BANNER_CACHE_TTL` = 600s + jitter). Acceptable: stale URLs still resolve through the local-FS fallback, and TTL expiry resolves the drift within minutes. No deliberate cache invalidation needed.

## Backfill / ops tooling

Two new subcommands in `scripts/r2_sync.py`, both gated by the existing `require_bulk_backfill()` (R2_ENABLED + R2_ALLOW_BULK_BACKFILL).

### `avatars-backfill`

```
uv run python scripts/r2_sync.py avatars-backfill [--dry-run] [--concurrency N]
```

- Walks `users` where `avatar != ''` AND `avatar_in_r2 = false`.
- For each user (concurrent up to N):
  - Read local file from `{AVATAR_STORAGE_PATH}/{filename}`. If missing, log `avatar_local_missing` and skip (bit stays false).
  - `head_object` for `avatars/{filename}` in `R2_PUBLIC_BUCKET`. If exists, skip upload (idempotent — content-addressed key).
  - Else `upload_file(local_path)`.
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
  - For each path: read `{BANNER_STORAGE_PATH}/{path}`. If any is missing, log `banner_local_missing` with the row's `banner_id` and skip the row entirely (bit stays false).
  - For each path: `head_object` then upload-if-missing as in avatars-backfill.
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
- Upload with `R2_ENABLED=true` and moto bucket: writes local AND R2, sets `avatar_in_r2 = true`.
- Upload with `R2_ENABLED=true` but R2 unavailable (closed moto port or patched `upload_bytes` raising): local write succeeds, `avatar_in_r2 = false`, request returns success, `avatar_r2_upload_failed` log captured.
- Orphan deletion with old `avatar_in_r2 = true`: local file unlinked AND R2 object deleted. With old `avatar_in_r2 = false`: R2 not touched.
- Orphan deletion when another user still references the avatar: neither local nor R2 touched.

### `tests/api/v1/test_users_avatar.py` (extend)

- Avatar URL on `UserResponse` switches to CDN URL when `R2_ENABLED=true` and `avatar_in_r2=true`; stays local otherwise.
- Privmsg responses include avatar URLs that respect the same rule (smoke-test a few of the six call sites).

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

1. Land the feature behind the existing `R2_ENABLED` flag — schema migrations, model bits, write-path dual-write, URL helpers, backfill subcommands, tests. With `R2_ENABLED=false` everywhere (current dev/staging default), behavior is unchanged: bits default false, fall-back URLs continue to work.
2. In production (`R2_ENABLED=true` already): run `avatars-backfill` and `banners-backfill` with `R2_ALLOW_BULK_BACKFILL=true`. Verify a random sample resolves through the CDN with `curl -I`.
3. New uploads from this point dual-write automatically; new banners require an `r2_sync.py banners-backfill` run to flip their bit.
4. (Future, out of scope) Once stable and IQDB/avatar/banner code paths can fully read from R2, demote local FS for these classes to read-only fallback or remove entirely.

## Risks and mitigations

- **R2 upload failure on the avatar hot path** — mitigated by log-and-continue semantics. Worst case: a small number of rows have `avatar_in_r2=false` and serve from local; backfill catches up.
- **Banner cache staleness after bit flip** — accepted; max ~15 minutes (TTL + jitter), and the fall-back URL remains valid through the staleness window.
- **Partial backfill leaves rows pointing at local** — by design. The bit guarantees URL correctness regardless of backfill state. Re-running the backfill is safe.
- **Forgotten privmsg call site** — six string-built URLs in `app/api/v1/privmsgs.py` is the largest concentration of inline URL building. Mitigated by the centralized helper plus tests for representative endpoints; a follow-up pass over the full file before merge confirms all sites are migrated.
- **Local-FS path for banners not configured today** — adding `BANNER_STORAGE_PATH` with a derived default keeps existing deployments working without a config change.

## Open questions

None at design time. Implementation will surface a few mechanical questions (exact log field names, helper module location for the avatar URL function) that fall through to the implementation plan.

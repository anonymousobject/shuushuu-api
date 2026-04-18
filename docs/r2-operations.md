# R2 Operations Runbook

See `docs/plans/2026-04-16-r2-image-serving-design.md` for the full design.
This file is the short reference for operators.

## Environment variables

| Name | Prod | Staging | Dev |
|------|------|---------|-----|
| `R2_ENABLED` | `true` | `true` | `false` |
| `R2_ALLOW_BULK_BACKFILL` | `true` | `false` | — |
| `R2_ACCESS_KEY_ID` | per-env | per-env | — |
| `R2_SECRET_ACCESS_KEY` | per-env | per-env | — |
| `R2_ENDPOINT` | R2 endpoint URL | same | — |
| `R2_PUBLIC_BUCKET` | `shuushuu-images` | `shuushuu-images-staging` | — |
| `R2_PRIVATE_BUCKET` | `shuushuu-images-private` | `shuushuu-images-staging-private` | — |
| `R2_PUBLIC_CDN_URL` | `https://cdn.e-shuushuu.net` | `https://cdn-staging.e-shuushuu.net` | — |
| `R2_PRESIGN_TTL_SECONDS` | `900` | `900` | — |
| `CLOUDFLARE_API_TOKEN` | per-env | per-env | — |
| `CLOUDFLARE_ZONE_ID` | per-env (prod zone) | per-env (staging zone) | — |

Staging's separate `CLOUDFLARE_ZONE_ID` ensures purges issued from staging
never affect prod. Staging's `R2_ALLOW_BULK_BACKFILL=false` ensures a
`reconcile` or `backfill-locations` (including inherited nightly crons)
cannot mass-upload prod-imported images into the small staging bucket.

## One-time cutover

1. Create `shuushuu-images-private` bucket.
2. Attach custom CDN domain to `shuushuu-images`.
3. Create a Cloudflare API token with zone-level cache purge permission.
4. Set all R2/Cloudflare env vars (flag still off).
5. Dry-run: `R2_ENABLED=true uv run python scripts/r2_sync.py split-existing --dry-run`
6. Run for real: `R2_ENABLED=true uv run python scripts/r2_sync.py split-existing`
7. Verify: `R2_ENABLED=true uv run python scripts/r2_sync.py verify --sample 1000`
8. Flip bulk-backfill on: set `R2_ALLOW_BULK_BACKFILL=true`.
9. Backfill: `uv run python scripts/r2_sync.py backfill-locations`
10. Flip `R2_ENABLED=true` in the app config, restart app + ARQ workers.
11. Monitor logs for `r2_*_failed` events for a week.

`R2_ALLOW_BULK_BACKFILL` stays `true` in prod permanently so the nightly
`reconcile` cron can heal stuck rows. Staging's `false` setting neutralises
the same cron on that environment.

## Common commands

```bash
# Inspect one image
R2_ENABLED=true uv run python scripts/r2_sync.py image 12345

# Audit recent rows
R2_ENABLED=true uv run python scripts/r2_sync.py verify --sample 1000

# Manual CDN purge
R2_ENABLED=true uv run python scripts/r2_sync.py purge-cache 12345

# Health (wire to monitoring)
R2_ENABLED=true uv run python scripts/r2_sync.py health --json
```

## Rollback

Set `R2_ENABLED=false`, restart app + workers. URLs fall back to `/images/*`
paths served from the local filesystem. R2 objects remain (harmless). DB
`r2_location` values stay (not consulted while the flag is off).

## Alerting thresholds

From `r2_sync.py health --json`:

- WARNING if `unsynced_count > 0` and `oldest_unsynced_age_seconds > 3600`
- CRITICAL if `unsynced_count > 100` or `oldest_unsynced_age_seconds > 21600`
- CRITICAL if `local_storage_used_bytes` exceeds your per-deployment ceiling

These thresholds are NOT useful on staging (which has millions of
`r2_location=NONE` rows by construction — prod DB copy, small R2 bucket).
Scope alerting to prod only, or add a staging-specific counting mode.

## Invariant to preserve in phase 2

When phase 2 removes local filesystem as source-of-truth, the
`local_cleanup_job` MUST refuse to delete any local file unless
`r2_location in {PUBLIC, PRIVATE}`. Do not add a fallback branch that deletes
based on age alone — a broken R2 integration then becomes a data-loss bug.

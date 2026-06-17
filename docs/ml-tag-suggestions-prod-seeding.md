# Production seeding runbook — ML tag suggestions

Seed prod's `ml_tag_suggestions` from the pre-computed GPU inference output
(`results.jsonl`) **without re-running inference**. Produces ~2M `pending`
suggestions for review.

This works because `results.jsonl` stores **external (Danbooru) tag names** plus
`image_id` — not internal tag IDs — so the external→internal mapping is resolved
at ingest time against *prod's* `tag_mappings`/`tags`. The `image_id`s line up
because the dev DB this was generated on is a restore of a prod backup.

**Key fact:** seeding (Stage 3 ingest) needs only the DB + `tag_mappings` + the
results file — **not** the ONNX model, and **not** `ML_TAG_SUGGESTIONS_ENABLED`.
The flag only gates *new-upload* generation. So you can seed prod with the flag
off, review internally, and enable live generation later (separately).

Reference numbers from the dev run: 1,051,908 results → **2,032,121 rows across
856,850 images, all pending, 0 errors** (avg 2.37/image; ~18% of images get
none, limited by the 257-tag `tag_mappings` set).

---

## 0. Pre-flight (once)

- [ ] Deploy `feat/ml-tag-suggestions` to the prod **app host** (the ingest
      script imports app modules: `store_predictions`, models, config).
- [ ] Confirm the results file is reachable from prod:
      `ls -lh /sakura/shuushuu/ml-backfill/results.jsonl` (≈2.8 GB, 1,051,908
      lines). If prod doesn't mount `/sakura`, copy it to local durable storage
      and adjust paths below.
- [ ] Take (or confirm) a recent prod DB backup. The seed is reversible (see
      Rollback), but back up anyway before a bulk write.
- [ ] Pick an **off-peak window** — this is ~2 h of elevated load (millions of
      small reads + ~2M inserts) on a live serving DB.
- [ ] Confirm you're targeting **prod**: `uv run python -c "from app.config import settings; print(settings.DATABASE_URL.split('@')[-1])"`
      (check the host) — the ingest writes to whatever `DATABASE_URL` resolves to.

## 1. Schema + mappings

- [ ] Apply the migration:
      `uv run alembic upgrade head`
      then verify: `uv run alembic current` includes `31b4f18cfd81`
      (`ml_tag_suggestions` + `tag_mappings` tables).
- [ ] Import the tag mappings (idempotent — skips existing):
      `uv run python scripts/import_tag_mappings.py data/tag_mappings.csv`
      Expect ~`Created: 297` (257 map + 40 ignore), `Errors: 0`.
- [ ] Sanity: `SELECT COUNT(*) FROM tag_mappings;` → ≈297.

## 2. Dry run (de-risk)

Validate the full path on prod with a small slice before committing 2 h:

```bash
head -2000 /sakura/shuushuu/ml-backfill/results.jsonl > /tmp/seed_sample.jsonl
uv run python scripts/ml_backfill_ingest.py /tmp/seed_sample.jsonl \
    --checkpoint /tmp/seed_sample.done > /tmp/seed_sample.log 2>&1
grep -E "processed=" /tmp/seed_sample.log     # processed=~2000 errors=N
```

- [ ] `errors` should be small (each = an image deleted from prod since the
      backup; the run skips it and continues). If `errors` is *large*, stop and
      investigate drift before the full run.
- [ ] These 2000 are real seed rows; the full run re-checks them idempotently
      (unique `(image_id, tag_id)` → 0 new), so no cleanup needed.

## 3. Full seed

Put the checkpoint + log on `/sakura` so a host reboot doesn't lose resume state.
Filter the per-image log noise (see Gotchas):

```bash
cat > /sakura/shuushuu/ml-backfill/seed_prod.sh <<'SH'
#!/bin/bash
cd /PATH/TO/shuushuu-api            # the deployed app dir on prod
uv run python scripts/ml_backfill_ingest.py \
    /sakura/shuushuu/ml-backfill/results.jsonl \
    --checkpoint /sakura/shuushuu/ml-backfill/ingest_prod.done \
    2>&1 | grep --line-buffered -vE "ml_suggestion_pipeline_|tag_mapping_|filter_redundant_" \
    > /sakura/shuushuu/ml-backfill/ingest_prod.log
SH
chmod +x /sakura/shuushuu/ml-backfill/seed_prod.sh
tmux new -d -s seed /sakura/shuushuu/ml-backfill/seed_prod.sh
```

- [ ] Monitor: `wc -l < /sakura/shuushuu/ml-backfill/ingest_prod.done` (of
      1,051,908), or `tmux attach -t seed`.
- [ ] Resumable: if it stops, re-run `seed_prod.sh` — it skips completed images
      via the checkpoint and tolerates a truncated final line.

## 4. Verify

- [ ] Summary: `grep -E "processed=" /sakura/shuushuu/ml-backfill/ingest_prod.log`
      → `processed=… created=… skipped=… errors=…`. `errors` = images deleted
      since the backup (expected small).
- [ ] DB:
      ```sql
      SELECT COUNT(*) AS rows,
             COUNT(DISTINCT image_id) AS imgs,
             SUM(status = 'pending') AS pending
      FROM ml_tag_suggestions;
      ```
      Expect on the order of ~2M rows / ~850k images / all pending (will be
      lower than dev by however many images drifted).
- [ ] Spot-check one image's suggestions look sane (API `GET
      /api/v1/images/{id}/ml-tag-suggestions` or SQL).

## 5. Go live for new uploads (separate, when ready)

Independent of the seed. Requires the ONNX **model on the prod worker host**
(seeding did not):

- [ ] Deploy `ml_models/<model>/model.onnx` (+ `selected_tags.csv`,
      `preprocess.json`) to the worker host.
- [ ] Set `ML_TAG_SUGGESTIONS_ENABLED=true` and restart the arq worker. It
      **fails to start** if the model files are missing (intentional).
- [ ] New uploads now enqueue generation; existing suggestions are untouched.

## Rollback

Pending suggestions are not applied to images. To undo the seed (scoped to the
seed model + pending, so human-reviewed rows are never touched):

```sql
DELETE FROM ml_tag_suggestions
WHERE model_version = 'swinv2_base_window8_256.dbv4-full'
  AND status = 'pending'
LIMIT 50000;   -- repeat in batches to avoid a long lock on a large table
```

## Gotchas

- **Logging noise**: `LOG_LEVEL=WARNING` does *not* quiet the per-image structlog
  lines (known bug: `app/core/logging.py` uses
  `make_filtering_bound_logger(logging.NOTSET)`). That's why step 3 filters the
  log. Fixing that bug first gives clean logs.
- **`Event loop is closed`** traceback at process exit is a harmless asyncio
  teardown artifact — all rows are already committed (per-image commit).
- **Re-seeding with expanded mappings** (more coverage) later: expand
  `tag_mappings`, clear the checkpoint, and re-run step 3 against the same
  `results.jsonl`. The unique constraint adds only the newly-mapped suggestions —
  still no GPU, no duplicates.
- **New prod images** (uploaded since the backup) aren't in `results.jsonl`; they
  get covered by new-upload generation once the flag is on, or a later
  incremental backfill of just those IDs.

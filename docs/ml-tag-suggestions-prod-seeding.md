# Production seeding runbook — ML tag suggestions

Seed prod's `ml_tag_suggestions` from the pre-computed GPU inference output
**without re-running inference**, and keep the raw predictions in prod so future
re-maps (mapping expansion, re-enabling character suggestions) are cheap and need
no GPU.

**Two-step, raw-store path (current architecture):**

1. `ml_raw_ingest` loads the GPU output (`results.jsonl`) into the lossless
   `ml_raw_predictions` store (+ `ml_external_tags` vocabulary).
2. `ml_remap` derives `ml_tag_suggestions` from that store using prod's current
   `tag_mappings` and the live pipeline (alias/hierarchy resolution, redundancy
   filter, the character gate).

> **Superseded:** older revisions of this doc used `ml_backfill_ingest.py`, which
> writes suggestions directly and **skips** `ml_raw_predictions`. Don't use it for
> prod — without the raw store you cannot cheaply re-map later (mapping changes,
> character re-enable) and would have to re-ingest or re-infer. The two paths
> produce equivalent suggestions; only `ml_raw_ingest`→`ml_remap` persists the raw
> data.

**Why it works:** `results.jsonl` stores **external (Danbooru) tag names** plus
`image_id` — not internal tag IDs — so the external→internal mapping is resolved
at remap time against *prod's* `tag_mappings`/`tags`. The `image_id`s line up
because the dev box this was generated on is a restore of a prod backup.

**Key fact:** seeding (ingest + remap) needs only the DB, `tag_mappings`,
`results.jsonl`, and the model's `selected_tags.csv` (for the vocabulary) — **not**
the ONNX model, and **not** `ML_TAG_SUGGESTIONS_ENABLED`. That flag only gates
*new-upload* generation. So you can seed with the flag off, review internally, and
enable live generation later (§7).

Reference numbers from the dev run: ~1.05M results → ~2M raw-derived **theme**
suggestions across ~850k images, all `pending` (character suggestions are gated
off by default — see §3; the raw store still holds the character predictions for
later).

**Model:** the deployed model is the animetimm **`swinv2_base_window8_256.dbv4-full`**
(what dev/test and `results.jsonl` were produced with) — *not* the
`wd-swinv2-tagger-v3` code default. Use that name everywhere below and set
`ML_MODEL_NAME` to match (§3).

---

## 0. Pre-flight (once)

- [ ] Squash-merge and deploy `feat/ml-tag-suggestions` to prod (API first, then
      frontend) per `docs/deployment.md` — `make prod-migrate` then
      `make prod-deploy`. The ingest/remap scripts import app modules, so they run
      from the deployed app dir.
- [ ] Confirm the results file is reachable from prod:
      `ls -lh /sakura/shuushuu/ml-backfill/results.jsonl` (≈2.8 GB, ~1.05M lines).
      If prod doesn't mount `/sakura`, copy it to local durable storage and adjust
      paths. *(Alternative to re-ingesting the JSONL: `mysqldump` dev's
      `ml_raw_predictions` + `ml_external_tags` + `ml_models` and load into prod —
      image_ids align since dev is a prod restore. Then skip §4 and go to §5.)*
- [ ] Provision the model's `selected_tags.csv` on the **app host** — `ml_raw_ingest`
      reads it to populate `ml_external_tags`. It is not in the repo; copy
      `ml_models/swinv2_base_window8_256.dbv4-full/selected_tags.csv` from skinny/dev.
      (The full model dir — `model.onnx` + `preprocess.json` too — is only needed on
      the worker host for §6 delta inference and §7 live generation.)
- [ ] Take (or confirm) a recent prod DB backup. The seed is reversible (see
      Rollback), but back up anyway before a bulk write.
- [ ] Pick an **off-peak window** — ~2 h of elevated load (millions of small reads
      + ~2M inserts) on a live serving DB.
- [ ] Confirm you're targeting **prod**:
      `uv run python -c "from app.config import settings; print(settings.DATABASE_URL.split('@')[-1])"`.

## 1. Schema (via the app deploy)

- [ ] `make prod-migrate` (from the deploy) applies `alembic upgrade head`,
      creating the ML tables. Verify: `uv run alembic current` is at head and the
      history includes `31b4f18cfd81` (`ml_tag_suggestions`), `edb3f5912896`
      (`ml_raw_predictions`), and the later index/merge revisions.

## 2. Prod ML config (`.env`)

Set before the worker starts (env is read at startup):

- [ ] `ML_MODEL_NAME=swinv2_base_window8_256.dbv4-full` — **critical**; must match
      the model the raw data was produced with.
- [ ] `ML_CHARACTER_SUGGESTIONS_ENABLED=false` — character suggestions stay gated
      (mapping-quality work pending). The raw store keeps character predictions, so
      re-enabling later is just this flag + a re-run of §5.
- [ ] `ML_SUGGESTION_BADGE_ENABLED=false` — per-image thumbnail badge stays off.
- [ ] `ML_TAG_SUGGESTIONS_ENABLED=false` **for now** — seeding doesn't need it; flip
      to `true` only in §7 when going live for new uploads.

## 3. Mappings

- [ ] Import the tag mappings (idempotent — skips existing):
      `uv run python scripts/import_tag_mappings.py data/tag_mappings.csv`
      Expect ~`Created: 297`, `Errors: 0`.
- [ ] Sanity: `SELECT COUNT(*) FROM tag_mappings;` → ≈297.

## 4. Ingest the GPU output into the raw store

Dry run first (validate the path on a small slice):

```bash
head -2000 /sakura/shuushuu/ml-backfill/results.jsonl > /tmp/seed_sample.jsonl
uv run python scripts/ml_raw_ingest.py /tmp/seed_sample.jsonl \
    --model swinv2_base_window8_256.dbv4-full > /tmp/seed_sample.log 2>&1
```

- [ ] Confirm it populated `ml_external_tags` and inserted `ml_raw_predictions` rows
      for the sample with no errors. Re-runs are idempotent (composite PK skip).

Full ingest (put logs on `/sakura` so a reboot doesn't lose progress):

```bash
tmux new -d -s rawingest '
  cd /PATH/TO/shuushuu-api
  uv run python scripts/ml_raw_ingest.py \
      /sakura/shuushuu/ml-backfill/results.jsonl \
      --model swinv2_base_window8_256.dbv4-full \
      > /sakura/shuushuu/ml-backfill/raw_ingest_prod.log 2>&1
'
```

- [ ] Verify: `SELECT COUNT(*) FROM ml_raw_predictions;` (dev had ~25.5M) and
      `SELECT COUNT(*) FROM ml_external_tags;` (~12.5k, the model vocab).

## 5. Generate suggestions from the raw store

```bash
tmux new -d -s remap '
  cd /PATH/TO/shuushuu-api
  uv run python scripts/ml_remap.py \
      --model swinv2_base_window8_256.dbv4-full \
      --checkpoint /sakura/shuushuu/ml-backfill/remap_prod.done \
      > /sakura/shuushuu/ml-backfill/remap_prod.log 2>&1
'
```

- [ ] Resumable: re-running skips checkpointed image IDs; a row that fails (image
      deleted since the backup) is logged and skipped.
- [ ] Verify:
      ```sql
      SELECT COUNT(*) AS rows,
             COUNT(DISTINCT image_id) AS imgs,
             SUM(status = 'pending') AS pending
      FROM ml_tag_suggestions;
      ```
      Expect ~2M rows / ~850k images / all pending (theme only; lower than dev by
      whatever images drifted). Spot-check one image:
      `GET /api/v1/images/{id}/ml-tag-suggestions`.

## 6. CPU-infer the delta (images newer than the GPU run)

Images uploaded to prod since `results.jsonl` was generated aren't in the raw
store. The delta is expected small — CPU inference is fine. Needs the full model
dir on the host (`model.onnx` + `selected_tags.csv` + `preprocess.json`).

```bash
# 1. Manifest of images NOT covered by the GPU results:
uv run python scripts/ml_backfill_manifest.py --out delta.jsonl \
    --exclude-results /sakura/shuushuu/ml-backfill/results.jsonl
# 2. CPU inference → JSONL:
uv run python scripts/ml_backfill_infer.py delta.jsonl \
    --model swinv2_base_window8_256.dbv4-full --out delta_results.jsonl
# 3. Into the raw store, then remap those images:
uv run python scripts/ml_raw_ingest.py delta_results.jsonl \
    --model swinv2_base_window8_256.dbv4-full
uv run python scripts/ml_remap.py --model swinv2_base_window8_256.dbv4-full \
    --checkpoint /sakura/shuushuu/ml-backfill/remap_prod.done
```

- [ ] The checkpoint from §5 means remap only processes the newly-ingested delta
      images.

## 7. Go live for new uploads (separate, when ready)

Independent of the seed. Requires the ONNX model on the prod **worker** host:

- [ ] Confirm `ml_models/swinv2_base_window8_256.dbv4-full/{model.onnx,
      selected_tags.csv, preprocess.json}` is present on the worker host.
- [ ] Set `ML_TAG_SUGGESTIONS_ENABLED=true` and restart the arq worker. It **fails
      to start** if the model files are missing (intentional).
- [ ] New uploads now enqueue CPU generation; existing suggestions are untouched.

## Rollback

Pending suggestions are not applied to images. To undo the derived suggestions
(scoped to the seed model + pending, so human-reviewed rows are never touched):

```sql
DELETE FROM ml_tag_suggestions
WHERE model_version = 'swinv2_base_window8_256.dbv4-full'
  AND status = 'pending'
LIMIT 50000;   -- repeat in batches to avoid a long lock on a large table
```

To also drop the raw store (only if fully abandoning): `TRUNCATE ml_raw_predictions;`
(and optionally `ml_external_tags`). Keeping the raw store is harmless and is what
makes re-maps cheap, so normally leave it.

## Gotchas

- **`ML_MODEL_NAME` mismatch** is the classic footgun: the code default is
  `wd-swinv2-tagger-v3`, but the raw data + everything below use
  `swinv2_base_window8_256.dbv4-full`. If they disagree, remap finds no raw rows
  for the configured model and live generation infers with the wrong model.
- **`selected_tags.csv` is not in the repo** (it's downloadable/large model data,
  gitignored). Provision it with the model files — `ml_raw_ingest` needs it for the
  vocabulary even though it doesn't need `model.onnx`.
- **Logging noise**: per-image structlog lines aren't quieted by `LOG_LEVEL`
  (`app/core/logging.py` uses `make_filtering_bound_logger(logging.NOTSET)`).
  Filter the ingest/remap logs (`grep -vE "ml_suggestion_pipeline_|tag_mapping_|filter_redundant_"`)
  or fix that first for clean logs.
- **`Event loop is closed`** at process exit is a harmless asyncio teardown
  artifact — rows are already committed (per-image commit).
- **Re-mapping later** (expanded `tag_mappings`, or re-enabling character
  suggestions): just re-run §5 (`ml_remap`) against the existing raw store —
  no GPU, no re-ingest. Character re-enable = `ML_CHARACTER_SUGGESTIONS_ENABLED=true`
  then re-run §5.

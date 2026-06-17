# ML Raw Predictions Store + Re-map

**Date:** 2026-06-17
**Repo/branch:** shuushuu-api, `feat/ml-tag-suggestions` (extends the ML tag-suggestion feature; not yet merged)
**Status:** Design — foundation scope (see Deferred)

## Overview & Goals

**Problem.** Today the model's raw predictions are transient: the live pipeline and the offline backfill (`ml_backfill_infer.py`) both run inference, map Danbooru tags → internal tags via `tag_mappings`, store the *mapped* result in `ml_tag_suggestions`, and discard the raw predictions. So any change to `tag_mappings` — e.g. adding new theme mappings, or **character mappings** — requires re-running full inference over the corpus to surface the newly-mapped tags. Inference over ~1M images is hours on GPU / days on CPU.

Two further constraints make this acute:
- The existing `results.jsonl` (2.8 GB, 1,051,908 images, 24,676,243 predictions) is **theme-only** — inference filtered to the general category (`{ANIMETIMM_GENERAL}`). It contains **zero character predictions**, so testing character tags can't reuse it; it needs a re-inference pass that includes the character category.
- Mapping coverage is currently tiny: only ~256 of the model's 12,476 vocabulary tags map to internal tags. Iterating coverage is exactly the workflow we want to make cheap.

**Solution.** Persist raw per-image predictions (general + character) in a queryable table. Then:
- Expanding `tag_mappings` (theme or character) → a cheap **re-map** (read raw preds → apply current mappings → upsert suggestions), no re-inference. Minutes, not hours/days.
- Raw predictions become queryable for evaluation (model coverage, per-category breakdowns, model-A-vs-B comparisons).

Full inference is needed only when the **model changes** (raw predictions differ). The per-prediction model id lets a re-map detect staleness.

This is all backend. The frontend gets character suggestions **for free**: the review panel renders by `tag.type`, so a character-typed internal tag displays with character styling and is gated to the same reviewers (`IMAGE_TAG_ADD`).

## Scope

**In (foundation):**
- `ml_external_tags`, `ml_models` dictionary tables + `ml_raw_predictions` table + migration.
- A re-inference pass capturing **general + character** raw predictions (run on the GPU box).
- `ml_raw_ingest` — load inference output into the tables (idempotent).
- `ml_remap` — re-map images from the table through current `tag_mappings` into `ml_tag_suggestions`.

**Out / Deferred (tracked so they aren't lost):**
- **Live-path persistence** — the upload/`generate` path writing raw preds to the table on every new image. *Until built, newly uploaded images still get theme-only live suggestions; only the backfilled corpus is character-re-mappable.*
- **Per-image "Re-run suggestions" button** (frontend) sourced from the table.
- **Lower-floor storage** — storing predictions below their per-tag thresholds so confidence-threshold tuning is also cheap (no re-infer). Foundation stores at the current threshold policy only.
- **Rating category** (9) — excluded.
- **swinv2 (and other-model) A/B passes** — the schema is multi-model (`model_id`); capturing additional models for per-image comparison is a cheap follow-on (`--model` per pass), not done now.

## Schema (3 tables)

**`ml_external_tags`** — the model's vocabulary dictionary (~12,476 rows; populated from the model's `selected_tags.csv`).
- `id` INT UNSIGNED PK
- `name` VARCHAR(255) NOT NULL, UNIQUE
- `category` SMALLINT NOT NULL — the model's own category (0 = general, 4 = character)

**`ml_models`** — model-version dictionary (a handful of rows).
- `id` SMALLINT UNSIGNED PK
- `name` VARCHAR(100) NOT NULL, UNIQUE — e.g. `swinv2_base_window8_256.dbv4-full`

**`ml_raw_predictions`** — one row per (image, model, predicted tag). ~24.7M rows now (general), more after the general+character re-infer. ≈1 GB.
- `image_id` INT UNSIGNED NOT NULL — FK → `images.image_id` ON DELETE CASCADE
- `model_id` SMALLINT UNSIGNED NOT NULL — FK → `ml_models.id`
- `external_tag_id` INT UNSIGNED NOT NULL — FK → `ml_external_tags.id`
- `confidence` FLOAT NOT NULL — float32 is ample for a 0–1 probability
- **PK (composite, clustered):** `(image_id, model_id, external_tag_id)` — per-image reads are a PK-prefix scan; supports multiple model versions; no separate secondary index needed for the re-map/lookup path.

`category` deliberately lives on `ml_external_tags` (a property of the tag), not on every prediction row — keeps the big table narrow. Cross-corpus queries join the dictionary.

**Sizing rationale:** ~31 B/row × ~24.7M ≈ ~0.8 GB, ~1–1.3 GB with fill-factor. Smaller than the 2.8 GB file because the file repeats JSON keys and the 35-char model-version string on every prediction; the table normalizes both away. `innodb_buffer_pool` is 2 GB (sized for the ~1 GB images table) — the re-map is a sequential scan and per-image lookups are PK seeks, so neither needs the whole table resident.

## Data flow

### Populate (GPU box; one-time now, repeat only on model change)
1. **`ml_backfill_infer.py` (modified)** — capture **raw** predictions across **general + character**. Today it calls `generate_suggestions` (general-only, mapped-shape); change it to use `model.predict(include_categories={GENERAL, CHARACTER})`, which returns `{tag, confidence, category}`. The **live** suggestion path (`generate_and_store_suggestions`) stays theme-only and untouched. Floor unchanged: per-tag `best_threshold` floored at `ML_MIN_CONFIDENCE`. Output → `results.jsonl` (now carrying `category`).
2. **`ml_raw_ingest` (new)** — load the JSONL → upsert `ml_external_tags` (by name) + `ml_models` (by name) + `ml_raw_predictions`. Idempotent by PK; re-inferring an image overwrites its raw rows. **Performance:** at ~24.7M+ rows, insert via batched `executemany` / `INSERT ... ON DUPLICATE KEY UPDATE` (or per-image-batch `INSERT IGNORE`), **not** per-row ORM `add()`s. Dictionary upserts use `INSERT ... ON DUPLICATE KEY UPDATE` (or insert-then-select) so concurrent/ resumed runs don't race a naive get-or-create.

**Model for this pass: `caformer_b36.dbv4-full`** (already downloaded; same 12,476-tag vocab as swinv2, so the dictionary is shared). Preprocessing is **already data-driven** — `AnimetimmModel` loads each model's `preprocess.json` via `animetimm_preprocess.load_test_pipeline` (caformer → 384px; swinv2 ships none and falls back to the default that mirrors its 448px spec), so caformer is a drop-in. **One code fix required:** add `caformer` to the animetimm dispatch in `ml_service.py` (today only `swinv2_*`/`convnext*` route to `_load_animetimm`, so a `caformer*` name raises `Unknown ML_MODEL_NAME`). Select the model via `ML_MODEL_NAME` or a new `--model` flag on `ml_backfill_infer.py`.

### Re-map (cheap, repeatable, against the DB — the iteration loop)
**`ml_remap` (new service + CLI)** — for a set of images (all, or a filter such as "missing character tags"): read raw preds from `ml_raw_predictions` (join the dictionary for name/category), compute the **implied internal suggestion set** via the existing resolution helpers — `resolve_external_tags` (`tag_mappings` → internal tag id), `resolve_tag_relationships` (alias/hierarchy), `filter_redundant_suggestions`, and the existing "exclude tags already applied to the image" rule — then run `ml_remap`'s **own reconciliation** (see "Re-map semantics") against `ml_tag_suggestions`. Run it after each `tag_mappings` change.

**Reuse boundary (important — do not skip):** `ml_remap` must **not** reuse `ml_suggestion_pipeline.store_predictions` / `ml_backfill.ingest_results` for the *write*. Those are purely additive (skip any tag that already has a row) **and** reset `approved → pending` for tags removed from the image — both of which conflict with this feature's reconcile rules below. Instead, **extract the prediction→implied-set computation** (the resolve + redundancy + exclude-applied middle of `store_predictions`) into a **shared helper** used by both the live pipeline and `ml_remap` (DRY), and give `ml_remap` its own write/reconcile step. Note the exclude-already-applied check currently lives *inside* `store_predictions`' write loop — extracting the helper means lifting it out into the shared computation so both callers get a consistent implied set.

Character suggestions therefore require both: (a) character raw preds in the store (from the general+character re-infer), and (b) character rows in `tag_mappings` (Danbooru character → internal character tag). The internal tag's `type` (set by the mapping target) drives the suggestion's `tag.type`, which the frontend renders accordingly.

## Re-map semantics (confirmed)

For each image, `ml_remap` computes the **implied set** `S` = internal tags produced by current mappings after `resolve_external_tags` → `resolve_tag_relationships` → `filter_redundant_suggestions`, excluding tags already applied to the image. Note `S` is **image-state-dependent** (the redundancy filter and applied-tag exclusion depend on the image's current tags), not mappings-only. Then it reconciles `ml_tag_suggestions` for that image:
- **Add** a `pending` row for each tag in `S` that has **no** existing suggestion row (any status). Each added row stores the **resolved** confidence (raw × the mapping's weight, × any hierarchy/parent multiplier `resolve_tag_relationships` applies) — *not* the raw prediction value — and `model_version` = the model name from the `ml_models` join (both are NOT NULL on `ml_tag_suggestions`).
- **Delete** existing `pending` rows whose tag is **not** in `S` (a removed/changed mapping — or a tag that became redundant/was applied — cleans up its stale pending).
- **Preserve** `approved` and `rejected` rows untouched, regardless of `S` — never clobber a human decision; a dismissed tag already has a row, so the "add" rule never re-suggests it.

This is "regenerate the pending set, preserve reviewed," chosen over purely-additive so iterating on mappings doesn't accumulate stale pending suggestions.

**Deliberate divergence from the live pipeline:** `store_predictions` resets `approved → pending` when an approved tag was since removed from the image. `ml_remap` does **not** — it leaves all reviewed rows alone. Re-map's job is to surface tags newly produced by changed mappings, not to re-litigate prior approvals against current image state; that reset stays a live-generation concern. (If we later want it in re-map too, it's an additive change.)

## Migration

One Alembic revision: create `ml_external_tags`, `ml_models`, `ml_raw_predictions` (with FKs + composite PK). Pre-populate `ml_external_tags` from the active model's `selected_tags.csv` and seed `ml_models` with the current model name (or let `ml_raw_ingest` upsert them).

**Column types (MariaDB):** unsigned id columns must use the dialect type `mysql.INTEGER(unsigned=True)` (not portable `sa.Integer`), and **every FK column must match the width/signedness of the PK it references or MariaDB rejects the constraint** (cf. the existing `# user_id must be UNSIGNED to match users.user_id` precedent). So: `ml_raw_predictions.image_id` uses `mysql.INTEGER(unsigned=True)` to match `images.image_id`; `external_tag_id` likewise matches `ml_external_tags.id` (INT UNSIGNED); `model_id` uses `mysql.SMALLINT(unsigned=True)` to match `ml_models.id` (SMALLINT UNSIGNED) — use the dialect SMALLINT call, not `sa.SmallInteger`.

## Testing

Service-level pytest against the test DB (real DB, no mocks — house rule):
- **raw-ingest:** dictionary upsert (new tag/model), `ml_raw_predictions` upsert + idempotency (re-ingest is a no-op), category recorded on the dictionary.
- **re-map:** mappings applied; a newly-added character mapping surfaces a character-typed pending suggestion; an `approved`/`rejected` tag is preserved and not re-suggested; a removed mapping's `pending` suggestion is cleaned up; an `approved` tag whose image-tag was removed is **left approved** (the deliberate divergence — not reset to pending).
- **multi-model:** re-ingesting an image under a different `model_id` adds rows alongside the prior model (composite PK), so a re-map/re-infer decision can tell a model change from a mappings-only change.

## Decisions log

- **Categories:** general (0) + character (4); rating (9) excluded.
- **Floor:** per-tag `best_threshold` floored at `ML_MIN_CONFIDENCE` (current policy, extended to character). Lower-floor storage deferred.
- **Re-map semantics:** regenerate pending, preserve reviewed, never re-suggest a dismissed tag.
- **Schema:** normalized rows + external-tag dictionary (queryable for eval), over a JSON-blob-per-image store.
- **Re-map reconcile is its own code, not `store_predictions`:** the shared part is the prediction→implied-set computation (extracted into a helper); the write/reconcile differs. `ml_remap` preserves all reviewed rows and does **not** replicate the live approved→pending-on-removal reset.
- **Model staleness:** `model_id` per row lets a re-map/re-infer decision detect a model change (re-infer) vs. a mappings-only change (re-map).
- **Populate model:** `caformer_b36.dbv4-full` (caformer-only this pass). Preprocessing is already data-driven (`preprocess.json` via `animetimm_preprocess`); the only code fix is adding `caformer` to the `ml_service` dispatch. swinv2 A/B deferred.

## Sequencing

1. Migration + dictionaries.
2. Modify `ml_backfill_infer.py` for general+character raw capture.
3. `ml_raw_ingest`.
4. Re-infer on the GPU box → ingest into the tables.
5. `ml_remap` service + CLI.
6. Add character rows to `tag_mappings`; re-map; evaluate.

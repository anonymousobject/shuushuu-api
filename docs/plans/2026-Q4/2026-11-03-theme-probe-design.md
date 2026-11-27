# Theme-tag linear probe on frozen swinv2 embeddings

Date: 2026-11-03
Status: approved 2026-11-03; implementation plan follows
Related: `docs/ml-tag-suggestions.md`; `docs/ml-tag-suggestions-prod-seeding.md`; `scripts/ml_gpu_infer.py`; `app/services/animetimm_model.py`; `app/services/ml_suggestion_pipeline.py`; ADR-0001

## Motivation

ML tag suggestions come from `swinv2_base_window8_256.dbv4-full`, an animetimm
model trained on Danbooru. Its 12,476 outputs are Danbooru tags. Shuushuu's
theme tags are a different vocabulary, so the pipeline translates through
`tag_mappings`: one external tag maps to one internal tag, with an optional
confidence multiplier. The table has 2,268 rows today, 385 of them pointing at
theme tags (332 distinct canonical theme tags once aliases collapse) and 1,833
at characters.

The translation works, mostly, but a lookup table can only express a 1:1
relationship. Many shuushuu theme tags are broader, narrower, or sideways of
any single Danbooru tag, and some have no Danbooru counterpart at all. Every
improvement so far has meant hand-curating more rows against the same
structural limit.

The question is whether a classifier trained on shuushuu's own labels does
better than the table. We have the data to find out:

| | |
|---|---|
| active images | 1,057,157 |
| tag links | 14.9M (14.1 per image), 11.0M of them theme tags |
| theme tags | 412 (65 aliases, 103 with a parent) |
| canonical theme tags with ≥ 100 images | 345 |
| images with at least one theme tag | all but 13 |

Theme tagging on shuushuu is dense, which matters: a missing label is much
less likely to be a tagging gap than it would be for a sparse vocabulary.

The ONNX graph already exposes a 1024-dimensional `embedding` output, the
pooled feature vector the Danbooru head consumes. A head trained on that
vector is the cheapest possible experiment: no backbone training, no ONNX
export, and if it wins it deploys as one small matrix multiply on an output
the worker already computes.

## Decision

Train a linear head over the frozen 1024-d embedding for shuushuu's canonical
theme tags, and compare it against the mapping path on identical held-out
images, tag by tag. The deliverable is the comparison and a go/no-go on
whether a learned head should become the primary suggestion mechanism.

The bar, from the project owner: accuracy decides. Cost would only veto a
clearly more accurate head if it were dramatically slower or heavier, and a
linear head on an existing output cannot be either.

### Approaches considered

- **A. Keep curating `tag_mappings`.** Zero new machinery, but it cannot fix
  the non-1:1 cases, which are the complaint. Stays as the fallback either way.
- **B. Fine-tune the backbone on shuushuu labels.** The strongest option on
  paper and the most expensive: multi-day training on a consumer ROCm card
  that has already shown runtime fragility, plus an ONNX re-export and a new
  drift surface. Premature until we know whether the frozen embedding already
  separates shuushuu's tags. Possible follow-up.
- **C. Train a new model from scratch.** Rejected. One million images is
  enough to adapt a pretrained backbone, not to replace seven million of
  pretraining.
- **D. Linear probe on the frozen embedding (chosen).** Hours of GPU forward
  passes, minutes of CPU training, numpy only, and the result is directly
  deployable. A small MLP head is a one-line follow-up if the linear probe is
  promising but plateaus.

## Goals

1. A reusable embedding store: the `embedding` vector for every active image
   with a thumbnail, written resumably, with the ids beside it.
2. A trained linear head for canonical theme tags with per-tag thresholds,
   saved as a single artifact with everything needed to apply it.
3. A side-by-side evaluation against the mapping path on the same test
   images: per-tag precision, recall and F1, macro and micro aggregates, and
   a count of tags each system wins.
4. A written decision.

## Non-goals

- Fine-tuning any part of the backbone.
- Character, artist or source tags. Theme tags only.
- Deploying the head into the worker or API, schema changes, new endpoints.
- Storing embeddings in Postgres, or similarity search over them.
- Replacing or editing `tag_mappings`.

## Anti-goals

The experiment fails even if the code works when:

- the comparison is not apples to apples: different image sets, different
  label resolution, or thresholds chosen on the images being scored;
- the result cannot be reproduced: no recorded split, seed, or model version;
- the head is trained or evaluated on labels that disagree with how the site
  treats aliases and hierarchy.

## Constraints

- **Two Pythons.** GPU inference runs on skinny in `~/ml-rocm` (Python 3.12,
  onnxruntime-rocm 1.21.0, ROCm 6.4.4), app-free. Training and evaluation run
  in the repo's uv venv (Python 3.14, CPU onnxruntime, numpy). No torch,
  scikit-learn or pandas is added; a logistic head needs none of them.
- **GPU environment.** `HSA_OVERRIDE_GFX_VERSION=10.3.0` is mandatory. The
  Ryzen 7600X's integrated GPU shows up as a second ROCm agent; without the
  override onnxruntime launches kernels there and dies with
  `hip_global.cpp:158 Module not initialized` on the first `run`. The process
  must also `os._exit(0)` after writing, because ROCm teardown corrupts the
  heap. Both are already in `~/ml-backfill/run-swinv2.sh`.
- **Throughput.** Measured 37 img/s at batch 1 on the GPU alone; batching to
  16 or 32 was slower. The June run averaged 14.5 img/s end to end, limited by
  single-threaded NFS reads and preprocessing. Expect 8 to 20 hours for the
  full corpus depending on prefetching.
- **Memory.** Skinny has 30 GB with about 6 GB free and no swap. 1.06M × 1024
  float16 is 2.2 GB; the same in float32 is 4.3 GB. Training streams from a
  memmap in minibatches and never materialises the full matrix in float32.
- **Images.** Thumbnails (≤ 500 px long edge, webp) under the same storage
  root as the June run. They are already near the 448 px input and are what
  the deployed predictions were made from.
- **Labels** come from the dev database on skinny, a prod restore.

## The pieces

### 1. Embedding extraction

Extend `scripts/ml_gpu_infer.py` rather than add a runner. It already handles
the manifest, sharding, resumability, the preprocessing copy guarded by
`tests/integration/test_ml_gpu_infer.py`, and the ROCm exit quirk.

- New `--embeddings-out DIR`. When given, each processed image's `embedding`
  output is appended to the store. `--out` becomes optional so the extraction
  run does not rewrite the 3 GB predictions JSONL that the raw store already
  holds.
- Store format: `embeddings.f16` (raw little-endian float16, row-major
  N × 1024), `embeddings.ids` (raw int64, N), and `embeddings.meta.json`
  recording model name, dimension, dtype, image variant and storage root.
  Append per batch, flush after each. On resume, the usable row count is
  the smaller of the two files' counts and both are truncated to it.
- Prefetch decoded tensors with a small bounded thread pool so the GPU is not
  waiting on NFS. This is the difference between 20 hours and 8. Keep it
  simple; a bounded `ThreadPoolExecutor.map` window is enough.
- Manifest: a fresh one from `scripts/ml_backfill_manifest.py` over active
  images, since the June manifest predates about 5,000 uploads.

### 2. Labels

From `tag_links` joined to `tags` where `type = 1`:

- **Aliases collapse.** A link to an alias tag counts for its canonical tag
  (`alias_of`). Only canonical tags become outputs. In the dev data no alias
  tag carries a link, so this matters only on the mapping side, where some
  `tag_mappings` rows point at alias tags.
- **Hierarchy closes upward.** `inheritedfrom_id` points at a tag's parent. A
  link to a child marks every ancestor positive. This is the site's own
  semantics: the pipeline treats a suggested parent as redundant when the
  child is present. The same closure is applied to both systems' predictions
  at evaluation time, so hierarchy is not a confound. Theme chains are at
  most two deep and never cross tag types.
- **Minimum support.** A tag is trained only if it has at least 100 positives
  in the training fold. Images with no theme tag after resolution are
  dropped (13 of them).

### 2b. Label provenance and masking

The review queue has been feeding `tag_links` since June, and that
contaminates a naive comparison in both directions.

**Approved suggestions are baseline true positives by construction.** 127,038
suggestions have been approved; 126,513 of them are theme tags still linked,
and 12,458 of those pairs fall in the test fold. For some tags they are most
of the test positives: `slippers` 35 of 35, `pocky` 94%, `scissors` 93%,
`headband` 92%, `bag` 76%. Twenty-eight tags get at least a tenth of their
test positives this way. On those tags the baseline's recall is self-fulfilling
and so is its best-threshold selection.

**Pending suggestions are mostly true, and scored as false positives.**
2,285,105 suggestions are pending. In the test fold alone, 229,534 pending
theme pairs touch about 85% of test images. Reviewed outcomes run 96.6%
approve, and even allowing for reviewers picking easy ones, most pending pairs
are real. Under "absent means negative" every one of them is a baseline false
positive, while the probe, trained to reproduce the labels gaps included, is
rewarded for not predicting them. Measured precision does not mean the same
thing for the two systems.

Decisions, made here so the evaluation is built for them from the start:

- Labels are built per (image, tag) pair with a **provenance flag**: linked by a
  human directly, linked via an approved suggestion, pending suggestion
  (unlinked), rejected suggestion (unlinked), or neither.
- **Training** uses all linked pairs as positives, approved ones included.
  They are human-verified and there is no reason to withhold them from the
  head.
- **Validation and test scoring accept a per-pair mask.** Masked pairs count
  as neither positive nor negative for either system. Threshold selection on
  the validation fold respects the same mask as the test scoring it feeds.
- **Headline:** absent means negative, with approved-suggestion pairs masked.
  This measures how well each system reproduces what shuushuu's own taggers
  chose to apply, with the baseline's feedback loop removed. It understates
  the baseline's precision by however many pending pairs are true; the
  write-up says so and reports the next two views beside it.
- **Sensitivity view:** approved and pending pairs both masked. The upper
  bound on the baseline's precision, and close to trivial for it, since what
  remains of its positives is linked pairs and the 4,481 rejections.
- **Reviewed-pairs view:** restricted to the 131k pairs a human has ruled on.
  For each system, precision at its operating threshold and the area under
  the ROC curve for separating approved from rejected. Thin, because there
  are few rejections, and biased toward pairs the baseline chose to propose,
  but it is the only slice with confirmed negatives.

Each view is the same metrics code with a different mask. The headline is a
methodology call made in this design, not a request from the project owner,
and the report must make the three views easy to compare so the owner can
weigh them differently.

### 3. Split

Deterministic on `image_id`: `image_id % 10 == 0` is test, `== 1` is
validation, the rest is training. Ids are chronological, so this is a uniform
random split that anyone can recompute without a stored file.

This measures in-distribution accuracy. Deployment is on new uploads, so the
evaluation also reports the test metrics restricted to the newest 10% of test
images as a secondary slice. A true temporal hold-out is a follow-up if that
slice disagrees with the overall numbers.

### 4. The head

- Logistic regression: 1024 → K with bias, independent sigmoid per tag.
- Features standardised per dimension with mean and standard deviation from
  the training fold, stored in the artifact.
- Binary cross-entropy, mean over tags, no class weighting in the first run.
  Adam, minibatches of a few thousand rows read from the memmap, a small L2
  penalty, early stopping on validation loss.
- After training, a per-tag threshold is chosen on the validation fold to
  maximise that tag's F1, the same scheme the animetimm model uses for its
  own `best_threshold`.
- Artifact: one `.npz` holding weights, bias, feature mean and std, tag ids,
  thresholds, the model name the embeddings came from, and the split rule.

### 5. The baseline

The mapping path as deployed in dev, reconstructed offline for the test
images: `ml_raw_predictions` for this model, joined to `ml_external_tags`
for names, through `tag_mappings` (confidence multiplied by the mapping's
confidence), aliases collapsed, hierarchy closed. A tag is predicted when its
mapped confidence is at least `ML_MIN_CONFIDENCE` (0.35).

The redundancy filter and the already-applied exclusion are deliberately left
out: both read the image's current tags, which are the ground truth. The
parent-supersede filter is moot under closure.

Two baseline variants are reported: **as deployed** (fixed 0.35) and
**best-threshold** (per-tag threshold chosen on validation, exactly as for the
probe), so the table is not beaten only on an operating point nobody tuned.
The raw store holds only predictions above `max(best_threshold, 0.35)`, so the
baseline cannot be evaluated below that floor; that is the system as it runs.

Coverage is near complete: 267 active images have no raw-store rows, 29 of
them in the test fold. The store has no "processed, nothing above threshold"
marker, so these images are excluded from both systems' scoring rather than
guessed at. Predictions for the roughly 5,000 images uploaded since June came
from the worker's full-size path or the upload form's downscaled JPEG, not
from thumbnails; the report notes this, it does not change the plan.

### 6. Metrics and report

Per tag: test positives, true and false positives, false negatives,
precision, recall, F1, for the probe and both baselines, plus the winner where
the F1 gap is at least 0.05. Aggregates: macro-F1 over tags with at least 20
test positives, micro-F1 over all trained tags, and the number of tags each
system brings to F1 ≥ 0.5. Everything is reported under the headline mask,
the sensitivity mask and the reviewed-pairs view (section 2b), and again on
the newest-10% slice.

The evaluation writes a CSV and a markdown summary to the output directory.
The summary goes into the decision record when we take it (ADR via
`plan-close-out`), not into this plan.

### 7. What counts as better

The head wins if its macro-F1 is clearly above the best-threshold baseline on
the shared tag set and it does not lose a meaningful number of tags the table
currently gets right. A mixed result where each wins different tags is itself
useful: it argues for running both and letting the review queue see each
system's confident calls. A loss says the embedding does not carry what
shuushuu's vocabulary needs, and fine-tuning becomes the next question.

### 8. Cost, for the record

The live path already runs the full forward pass to get `prediction`.
Requesting `embedding` from the same `run` call adds nothing. The head is a
1024 × K float32 matrix, about 1.4 MB for K ≈ 345, applied once per image.
CPU-only prod would not notice.

## Phasing

1. **Smoke slice first.** Every 50th manifest row, roughly 21,000 images,
   through the whole pipeline: extract, train, evaluate. Proves the file
   format, the resume logic, the label query and the metrics in under an hour
   of GPU time before committing to the overnight run.
2. **Full extraction** overnight, sharded if useful, resumable.
3. **Train and evaluate**, then write up.

## Testing

TDD applies. The pieces have real logic worth guarding, none of it needs a
mock:

- embedding store: round trip, append, and resume after a truncated write;
- label building: alias collapse and hierarchy closure on a small fixture;
- split: deterministic and disjoint;
- metrics and threshold selection on hand-computed cases;
- trainer: recovers a planted linear separator on synthetic data;
- label provenance: each flag assigned correctly on a fixture that has a
  direct link, an approved suggestion, a pending one and a rejected one, and
  the masks derived from them;
- drift guard: `tests/integration/test_ml_gpu_infer.py` gains an assertion
  that the standalone runner's embedding equals `AnimetimmModel`'s on the real
  model (skipped when the model is absent, like its neighbours). That means
  `AnimetimmModel` gains a way to return the embedding; a small change, not a
  deployment.

## Left to the builder

- Exact CLI shape: one `scripts/ml_probe.py` with `labels`, `train` and
  `eval` subcommands is the suggestion; separate scripts are fine.
- Where the pure logic lives. `app/services/ml_probe.py` is the suggestion,
  so a winning head's loader is already where the worker would import it.
  The store writer stays in the app-free runner; the reader lives in the app.
- Batch size, learning rate, L2 strength, epoch cap, threshold sweep grid.
- How to pull about 9M theme-tag links from Postgres without exhausting
  memory (server-side cursor or `COPY`).
- Prefetch window size and whether to shard the extraction.
- Output directory layout under `~/ml-backfill/` and whether to mirror to
  `/sakura`.

## Risks

- **Memory.** The dev stack, ollama and the page cache already use most of
  the 30 GB. Any step that loads the full embedding matrix as float32 will be
  killed. Memmap plus minibatches throughout.
- **NFS.** A million small reads over NFS is the slow part. Prefetch, and
  expect a handful of missing thumbnails (177 last time) to be skipped.
- **Label noise.** A shuushuu tag absent from an image is treated as a
  negative in the headline view. The review queue shows the gaps are real
  (section 2b), and the head will be conservative on tags users apply
  inconsistently. Per-tag thresholds absorb some of this; the sensitivity and
  reviewed-pairs views bound the rest. Using the rejections as hard-negative
  training labels is a possible follow-up, not part of the first run.
- **Feedback loop.** The baseline has been writing the labels since June.
  Section 2b masks its approved pairs out of scoring; nothing can be done
  about tags whose human tagging habits shifted because suggestions existed.
- **Baseline handicap.** The raw store's 0.35 floor caps what the mapping
  path can show. This is the deployed system's real operating envelope, and
  the report says so.
- **Curation leakage.** `tag_mappings` was curated by looking at dev data
  that overlaps the test fold. This can only flatter the baseline, never the
  probe.

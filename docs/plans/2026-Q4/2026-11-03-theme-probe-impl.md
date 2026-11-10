# Theme-tag linear probe — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the swinv2 `embedding` vector for every active image, train a numpy logistic head for shuushuu's canonical theme tags on it, and produce a per-tag comparison against the `tag_mappings` path on identical held-out images, under the three label masks the design defines.

**Architecture:** The app-free GPU runner (`scripts/ml_gpu_infer.py`) gains an append-only embedding store writer and a bounded prefetch. A new package `app/services/ml_probe/` holds the pure logic: store reader, split, label matrix with provenance, baseline reconstruction, metrics, trainer, report. One CLI, `scripts/ml_probe.py`, wires them into four subcommands (`labels`, `baseline`, `train`, `eval`), the first two needing the database and the last two only files.

**Tech Stack:** Python 3.14 + numpy in the repo venv for everything but extraction; Python 3.12 + onnxruntime-rocm in `~/ml-rocm` for extraction on skinny. SQLAlchemy async streaming for the two bulk reads. No torch, scikit-learn, scipy or pandas.

**Spec:** `docs/plans/2026-Q4/2026-11-03-theme-probe-design.md`

## Global Constraints

- Branch `feat/theme-probe` off `main`; everything here goes through a PR. The 1.2 GB model snapshot at the repo root (`swinv2_base_window8_256.dbv4-full-bucket/`) is untracked: `git add` named paths only, never `-A` or `.`.
- `app/` stays at zero mypy errors; `uv run mypy scripts/ml_gpu_infer.py scripts/ml_probe.py` must be clean. Ruff line length 100.
- `scripts/ml_gpu_infer.py` must import nothing from `app` and run under Python 3.12 with only numpy, pillow and onnxruntime. Any logic it shares with the app is a copy guarded by `tests/integration/test_ml_gpu_infer.py`.
- Numpy types: `from numpy.typing import NDArray`; `NDArray[np.float32]` etc.
- Embedding store: `embeddings.f16` (little-endian float16, row-major N×1024), `embeddings.ids` (int64, N), `embeddings.meta.json`. `EMBEDDING_DIM = 1024`.
- Split: `fold = image_id % 10`; `TEST_FOLD = 0`, `VAL_FOLD = 1`, train is fold ≥ 2.
- Provenance codes (uint8): `NONE = 0`, `LINKED = 1`, `APPROVED = 2`, `PENDING = 3`, `REJECTED = 4`.
- Hierarchy: `inheritedfrom_id` is the parent. Closure is upward (child ⇒ ancestors). Chains are ≤ 2 deep but code must iterate to a fixed point.
- Minimum training support 100 positives in the train fold; macro aggregates over tags with ≥ 20 test positives; baseline fixed threshold `0.35` (`settings.ML_MIN_CONFIDENCE`); winner needs an F1 gap ≥ `0.05`.
- DB tests run with `./run-tests.sh <path>` (serial) and use the `db_session` fixture; create rows with `Users`/`Images`/`Tags` models as `tests/services/test_ml_raw_store.py` does. Scripts run from the host need `DATABASE_URL` pointed at `localhost` (the `.env` value names the compose hostname `postgres`):
  ```bash
  export DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2- | sed 's/@postgres:/@localhost:/')"
  ```
- GPU runs on skinny: `HSA_OVERRIDE_GFX_VERSION=10.3.0 ~/ml-rocm/bin/python -u scripts/ml_gpu_infer.py ...`; the process ends with `os._exit(0)` (already in `run()`).

## Review Focus

1. A corrupt or missing thumbnail mid-run must skip that image without desyncing ids and vectors. Test: Task 2, `test_embeddings_out_skips_unreadable_image`.
2. A crash between the vector write and the id write must leave a store that reopens, truncates to the consistent count, and reprocesses the half-written image. Test: Task 1, `test_writer_resume_truncates_to_consistent_rows`.
3. The store and the label set will not hold the same image ids (deletions, missing files, newer uploads). Every join is by id, never by position. Test: Task 8, `test_train_and_eval_align_by_image_id`.
4. A tag with no positives in validation, or no predicted positives in test, must yield a threshold and precision of 0, never NaN or a crash. Test: Task 5, `test_best_thresholds_tag_without_positives`, `test_confusion_zero_predictions_precision_is_zero`.
5. An image with no raw-store rows is excluded from both systems' scoring, not counted as a baseline all-negative. Test: Task 4, `test_baseline_has_raw_false_for_unprocessed_image`; Task 7, `test_compare_drops_rows_without_raw`.

---

### Task 1: Embedding store — reader in the app, writer in the runner

**Files:**
- Create: `app/services/ml_probe/__init__.py` (module docstring only)
- Create: `app/services/ml_probe/store.py`
- Modify: `scripts/ml_gpu_infer.py` (add `EmbeddingWriter` below the JSONL helpers)
- Test: `tests/services/ml_probe/__init__.py` (empty), `tests/services/ml_probe/test_store.py`, `tests/integration/test_ml_gpu_infer.py`

**Interfaces:**
- Produces (`store.py`):
  ```python
  EMBEDDING_DIM = 1024
  VECTORS_FILE, IDS_FILE, META_FILE = "embeddings.f16", "embeddings.ids", "embeddings.meta.json"

  @dataclass
  class EmbeddingStore:
      ids: NDArray[np.int64]          # [N], in write order
      vectors: np.memmap              # float16 [N, dim], read-only
      meta: dict[str, Any]
      def rows_for(self, image_ids: NDArray[np.int64]) -> NDArray[np.int64]:
          """Row index per requested id, -1 where absent."""

  def consistent_row_count(directory: Path, dim: int) -> int:
      """min(len(ids), vector_bytes // (dim * 2)); 0 when files are absent."""
  def open_embedding_store(directory: Path) -> EmbeddingStore
      # reads meta for dim; memmaps the first consistent_row_count rows; raises
      # FileNotFoundError without meta
  ```
- Produces (runner):
  ```python
  class EmbeddingWriter:
      def __init__(self, directory: Path, dim: int, meta: dict[str, Any]) -> None
          # mkdir; write meta if absent, else require meta["model"] and dim to match
          # (ValueError otherwise); truncate both files to the consistent row count;
          # populate self.done_ids
      done_ids: set[int]
      def append(self, image_id: int, vector: NDArray[np.floating]) -> None
          # vector.astype(np.float16).tobytes() to .f16, flush; then int64 id to .ids, flush
      def close(self) -> None
  ```
  Meta keys written: `model`, `dim`, `dtype` (`"float16"`), `variant`, `storage_root`, `created_at` (ISO 8601 UTC).

- [ ] **Step 1: Create the branch**

```bash
cd /home/dtaylor/shuushuu-api && git checkout -b feat/theme-probe main
```

- [ ] **Step 2: Write the failing reader tests** in `tests/services/ml_probe/test_store.py`

```python
def test_open_store_reads_rows_and_meta(tmp_path):
    # hand-write meta {"model": "m", "dim": 4, "dtype": "float16"}, ids [5, 9], 2x4 float16 vectors
    store = open_embedding_store(tmp_path)
    assert store.ids.tolist() == [5, 9]
    assert store.vectors.shape == (2, 4) and store.vectors.dtype == np.float16
    assert store.rows_for(np.array([9, 5, 7])).tolist() == [1, 0, -1]

def test_consistent_row_count_uses_shorter_file(tmp_path):
    # 3 ids but only 2 full vectors + 3 stray bytes -> 2
    assert consistent_row_count(tmp_path, dim=4) == 2

def test_open_store_without_meta_raises(tmp_path):
    with pytest.raises(FileNotFoundError): open_embedding_store(tmp_path)
```

- [ ] **Step 3: Run them to verify they fail** — `./run-tests.sh tests/services/ml_probe/test_store.py` → ImportError.

- [ ] **Step 4: Implement `store.py`** per the interface. `vectors` is `np.memmap(path, dtype=np.float16, mode="r", shape=(n, dim))`; `rows_for` uses a dict or `np.searchsorted` over an argsort of `ids`.

- [ ] **Step 5: Run the reader tests** — PASS.

- [ ] **Step 6: Write the failing writer tests** in `tests/integration/test_ml_gpu_infer.py` (the module already loads the runner as `ml_gpu_infer`)

```python
@pytest.mark.unit
def test_writer_round_trips_through_reader(tmp_path):
    w = ml_gpu_infer.EmbeddingWriter(tmp_path, dim=4, meta={"model": "m", "variant": "thumbs", "storage_root": "/x"})
    w.append(5, np.arange(4, dtype=np.float32)); w.append(9, np.ones(4, dtype=np.float32)); w.close()
    store = open_embedding_store(tmp_path)
    assert store.ids.tolist() == [5, 9]
    assert np.allclose(store.vectors[0], [0, 1, 2, 3])
    assert store.meta["model"] == "m" and store.meta["dim"] == 4 and store.meta["dtype"] == "float16"

@pytest.mark.unit
def test_writer_resume_truncates_to_consistent_rows(tmp_path):
    # write 3 rows; append 8 bytes of a 4th vector to .f16 with no id (crash mid-record)
    w2 = ml_gpu_infer.EmbeddingWriter(tmp_path, dim=4, meta={"model": "m"})
    assert w2.done_ids == {1, 2, 3}
    assert (tmp_path / "embeddings.f16").stat().st_size == 3 * 4 * 2
    # and the mirror case: 4 ids but 3 vectors -> ids truncated to 3

@pytest.mark.unit
def test_writer_rejects_store_from_other_model(tmp_path):
    with pytest.raises(ValueError): ml_gpu_infer.EmbeddingWriter(tmp_path, dim=4, meta={"model": "other"})
```

- [ ] **Step 7: Run them to verify they fail** — `uv run pytest tests/integration/test_ml_gpu_infer.py -k writer -v` → AttributeError.

- [ ] **Step 8: Implement `EmbeddingWriter` in the runner.** Truncation uses `os.truncate`. Open both files in `"ab"` once; `append` writes vector bytes, flushes, writes id, flushes, so a crash can only leave a trailing partial vector.

- [ ] **Step 9: Run writer tests and the whole drift-test module** — PASS, existing tests untouched.

- [ ] **Step 10: Type-check and commit**

```bash
uv run mypy app/ scripts/ml_gpu_infer.py
git add app/services/ml_probe/__init__.py app/services/ml_probe/store.py scripts/ml_gpu_infer.py \
  tests/services/ml_probe/__init__.py tests/services/ml_probe/test_store.py tests/integration/test_ml_gpu_infer.py
git commit -m "feat(ml-probe): append-only embedding store, writer in the GPU runner"
```

---

### Task 2: Runner emits embeddings, with bounded prefetch; model wrapper exposes the embedding

**Files:**
- Modify: `scripts/ml_gpu_infer.py` (`predict`, `run`, `main`)
- Modify: `app/services/animetimm_model.py`
- Test: `tests/integration/test_ml_gpu_infer.py`

**Interfaces:**
- Produces (runner):
  ```python
  def infer(session, input_name: str, img_array: NDArray[np.float32], *, with_embedding: bool
            ) -> tuple[NDArray[np.float32], NDArray[np.float32] | None]
      # session.run(["prediction"] or ["prediction", "embedding"]) -> (probs[12476], embedding[1024] | None)
  def select_predictions(probabilities, names, categories, thresholds, min_confidence,
                         model_version, allowed_categories) -> list[dict[str, Any]]
      # the loop currently inside predict(); predict() keeps its signature and becomes
      # preprocess -> infer(with_embedding=False) -> select_predictions
  def bounded_prefetch[T, R](fn: Callable[[T], R], items: Iterable[T], *, workers: int, window: int
                             ) -> Iterator[tuple[T, R | BaseException]]
      # ThreadPoolExecutor; at most `window` futures in flight; yields in input order;
      # an exception from fn is yielded as the value, never raised
  ```
  CLI: `--embeddings-out DIR` (optional); `--out` no longer `required`, parser errors if neither is given; `--prefetch-workers N` (default 4, window = 4 × N). With `--embeddings-out`, the done-set comes from `EmbeddingWriter.done_ids`; otherwise from the results JSONL as today. Each processed image is written to the store first, then (if `--out`) to the JSONL.
- Produces (app): `AnimetimmModel.embed(image_path: str) -> NDArray[np.float32]` (async, `run_in_executor`, shape `[1024]`), via `_embed_sync` calling `session.run(["embedding"], ...)`.

- [ ] **Step 1: Write the failing unit tests**

```python
@pytest.mark.unit
def test_bounded_prefetch_preserves_order_and_yields_exceptions():
    def fn(i): 
        if i == 2: raise ValueError("bad")
        return i * 10
    out = list(ml_gpu_infer.bounded_prefetch(fn, [0, 1, 2, 3], workers=2, window=2))
    assert [item for item, _ in out] == [0, 1, 2, 3]
    assert out[1][1] == 10 and isinstance(out[2][1], ValueError)

@pytest.mark.unit
def test_cli_requires_out_or_embeddings_out():
    result = subprocess.run([sys.executable, str(_RUNNER), "--manifest", "m", "--model-dir", "d",
                             "--storage-path", "s"], capture_output=True, text=True)
    assert result.returncode == 2 and "--out" in result.stderr

@pytest.mark.unit
def test_select_predictions_matches_predict(monkeypatch, tmp_path):
    # same fixtures as test_predict_include_character_emits_general_and_character; assert
    # ml_gpu_infer.select_predictions(np.array(_PROBS), _NAMES, _CATEGORIES, _THRESHOLDS, 0.35,
    #   "test-model", {0, 4}) == ml_gpu_infer.predict(...)  # both paths agree
```

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement `infer`, `select_predictions`, `bounded_prefetch`, and rewire `run()`.** The prefetch function returns the preprocessed tensor for a manifest record (path resolution with the fullsize fallback stays as today; a missing file returns `None` so it is counted as `missing`, an exception counts as `failed`). Inference stays batch 1. Keep the existing `--help` text pattern for the new flags.

- [ ] **Step 4: Run the whole module's unit tests** — all existing `predict` tests still pass.

- [ ] **Step 5: Write the failing model-gated tests** (same `skipif` as `test_predict_matches_canonical`)

```python
async def test_embeddings_out_matches_canonical_embedding(tmp_path):
    # run the CLI with --embeddings-out (no --out) on the synthetic png; open the store;
    # await AnimetimmModel.embed(img_path); assert store.ids == [123] and
    # np.max(np.abs(store.vectors[0].astype(np.float32) - canonical)) < 2e-2  (float16 storage)
    # also assert (tmp_path / "r.jsonl") does not exist

async def test_embeddings_out_skips_unreadable_image(tmp_path):
    # manifest rows 1 (corrupt file) and 2 (good); store ids == [2]; stdout says "skipping image 1"

async def test_embeddings_out_resumes(tmp_path):
    # run once on ids [1, 2]; run again on manifest [1, 2, 3]; store ids == [1, 2, 3], stdout "2 already done"
```

- [ ] **Step 6: Implement `AnimetimmModel.embed` / `_embed_sync`** mirroring `predict` / `_predict_sync`.

- [ ] **Step 7: Run the gated tests with the model present**

```bash
ML_MODELS_PATH=/home/dtaylor/ml-models uv run pytest tests/integration/test_ml_gpu_infer.py -v
```
Expected: all PASS, none skipped.

- [ ] **Step 8: Type-check and commit**

```bash
uv run mypy app/ scripts/ml_gpu_infer.py
git add scripts/ml_gpu_infer.py app/services/animetimm_model.py tests/integration/test_ml_gpu_infer.py
git commit -m "feat(ml-probe): GPU runner writes embeddings with bounded prefetch"
```

---

### Task 3: Split and labels with provenance

**Files:**
- Create: `app/services/ml_probe/split.py`, `app/services/ml_probe/labels.py`
- Test: `tests/services/ml_probe/test_split.py`, `tests/services/ml_probe/test_labels.py`

**Interfaces:**
```python
# split.py
TEST_FOLD, VAL_FOLD = 0, 1
def fold_of(image_ids: NDArray[np.int64]) -> NDArray[np.uint8]        # image_ids % 10

# labels.py
NONE, LINKED, APPROVED, PENDING, REJECTED = 0, 1, 2, 3, 4

@dataclass
class ThemeTagGraph:
    tag_ids: NDArray[np.int64]            # [K] canonical theme tags (alias_of IS NULL), ascending
    titles: list[str]
    parent_index: NDArray[np.int64]       # [K] column of inheritedfrom_id, -1 if none
    column_of: dict[int, int]             # every theme tag id (aliases too) -> canonical column

async def load_theme_tag_graph(db: AsyncSession) -> ThemeTagGraph

def close_upward(matrix: NDArray, parent_index: NDArray[np.int64]) -> NDArray
    # bool: parent |= child; float: parent = max(parent, child); iterate to fixed point; returns a copy

@dataclass
class LabelSet:
    image_ids: NDArray[np.int64]          # [N] ascending
    tag_ids: NDArray[np.int64]; titles: list[str]; parent_index: NDArray[np.int64]
    provenance: NDArray[np.uint8]         # [N, K]
    def positives(self) -> NDArray[np.bool_]        # close_upward(provenance in {LINKED, APPROVED})
    def human_positives(self) -> NDArray[np.bool_]  # close_upward(provenance == LINKED)
    def approved_mask(self) -> NDArray[np.bool_]    # positives & ~human_positives
    def pending_mask(self) -> NDArray[np.bool_]     # (provenance == PENDING) & ~positives
    def rejected(self) -> NDArray[np.bool_]         # (provenance == REJECTED) & ~positives
    def save(self, path: Path) -> None              # np.savez_compressed
    @classmethod
    def load(cls, path: Path) -> "LabelSet"

async def build_label_set(db: AsyncSession, graph: ThemeTagGraph, *, status: int = ImageStatus.ACTIVE) -> LabelSet
    # images: status filter, filename IS NOT NULL, ordered by image_id
    # pass 1: stream tag_links joined to Tags.type == TagType.THEME for those images -> LINKED
    # pass 2: stream ml_tag_suggestions joined the same way -> APPROVED where LINKED already set and
    #         status == "approved"; PENDING / REJECTED where no link; a link with a pending or
    #         rejected suggestion stays LINKED
    # drop rows with no positives
```
Streaming: `await db.stream(stmt.execution_options(yield_per=100_000))` and `.partitions()`; map ids to rows with `np.searchsorted` on `image_ids` and tags with `graph.column_of`.

- [ ] **Step 1: Write failing pure tests**

```python
def test_fold_of(): assert fold_of(np.array([10, 11, 29])).tolist() == [0, 1, 9]

def test_close_upward_bool_two_levels():
    # parent_index = [-1, 0, 1] (2 -> 1 -> 0); matrix [[F, F, T]] -> [[T, T, T]]
def test_close_upward_float_takes_max():
    # [[0.1, 0.9]] with parent_index [-1, 0] -> [[0.9, 0.9]]; input not mutated

def test_label_set_masks():
    # 1 image, tags A (parent of B), B, C; provenance A=NONE, B=APPROVED, C=PENDING
    # positives == [T, T, F]; human_positives == [F, F, F]; approved_mask == [T, T, F]; pending_mask == [F, F, T]
def test_label_set_save_load_round_trip(tmp_path)
```

- [ ] **Step 2: Run to verify failure; implement `split.py`, `close_upward`, `LabelSet`; run to PASS.**

- [ ] **Step 3: Write the failing DB tests** (`db_session`; helpers like `tests/services/test_ml_raw_store.py`)

```python
async def test_load_theme_tag_graph_collapses_aliases_and_parents(db_session):
    # tags: parent P (type 1), child C (inheritedfrom_id=P), alias X (alias_of=C), character tag Z
    # graph.tag_ids == sorted([P, C]); column_of[X] == column_of[C]; parent_index[col C] == col P; Z absent

async def test_build_label_set_provenance_rules(db_session):
    # image 1: link C, approved suggestion C  -> APPROVED at C
    # image 2: link P, pending suggestion P   -> LINKED at P (link wins)
    # image 3: pending P, rejected C, no links -> no positives, so the row is dropped
    # image 4: link via alias X               -> LINKED at C
    # image 5: inactive (status 0) with links -> absent
    # image 6: link C, pending suggestion P   -> LINKED at C, PENDING at P (kept: has a positive)
    # assert image_ids == [1, 2, 4, 6]; provenance values as above
```

- [ ] **Step 4: Run to verify failure; implement `load_theme_tag_graph` and `build_label_set`; run to PASS.**

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy app/
git add app/services/ml_probe/split.py app/services/ml_probe/labels.py tests/services/ml_probe/test_split.py tests/services/ml_probe/test_labels.py
git commit -m "feat(ml-probe): theme-tag label matrix with provenance and upward closure"
```

---

### Task 4: Baseline reconstruction from the raw store

**Files:**
- Create: `app/services/ml_probe/baseline.py`
- Test: `tests/services/ml_probe/test_baseline.py`

**Interfaces:**
```python
@dataclass
class BaselineScores:
    image_ids: NDArray[np.int64]     # [M] ascending, the subset of requested ids
    scores: NDArray[np.float32]      # [M, K] mapped confidence, 0 where nothing maps; NOT closed
    has_raw: NDArray[np.bool_]       # [M] image has any raw row for the model
    def save(self, path: Path) -> None; @classmethod def load(cls, path) -> "BaselineScores"

async def build_baseline_scores(db: AsyncSession, graph: ThemeTagGraph, *, model_name: str,
                                image_ids: NDArray[np.int64]) -> BaselineScores
    # model_id from MlModels.name == model_name (ValueError if absent)
    # external_tag_id -> (column, mapping_confidence) from TagMappings joined MlExternalTags by name,
    #   internal_tag_id not null, through graph.column_of (non-theme targets ignored)
    # stream MlRawPredictions where model_id == ... and image_id in requested (chunk the IN list or
    #   filter in Python on a `image_id % 10 IN (...)` stream — builder's call);
    #   score[row, col] = max(score, confidence * mapping_confidence); has_raw[row] = True on any row
```

- [ ] **Step 1: Write the failing DB test**

```python
async def test_build_baseline_scores_maps_and_aggregates(db_session):
    # graph with P (parent of C); external tags "e1"->C conf 1.0, "e2"->X(alias of C) conf 0.5, "e3" unmapped
    # image 1 raw: e1 0.8, e2 0.9, e3 0.99  -> scores[C] == 0.8 (max(0.8, 0.45)), scores[P] == 0, has_raw True
    # image 3: not requested                 -> absent

async def test_baseline_has_raw_false_for_unprocessed_image(db_session):
    # requested image with no raw rows -> present, has_raw False, scores all 0

async def test_build_baseline_scores_unknown_model_raises(db_session)
```

- [ ] **Step 2: Run to verify failure; implement; run to PASS.**

- [ ] **Step 3: Type-check and commit**

```bash
git add app/services/ml_probe/baseline.py tests/services/ml_probe/test_baseline.py
git commit -m "feat(ml-probe): reconstruct the mapping-path scores from the raw store"
```

---

### Task 5: Metrics, thresholds, AUC

**Files:**
- Create: `app/services/ml_probe/metrics.py`
- Test: `tests/services/ml_probe/test_metrics.py`

**Interfaces:**
```python
@dataclass
class TagMetrics:
    tp: NDArray[np.int64]; fp: NDArray[np.int64]; fn: NDArray[np.int64]   # [K]
    @property precision / recall / f1 -> NDArray[np.float64]              # 0.0 where denominator is 0
    def support(self) -> NDArray[np.int64]                                # tp + fn

def confusion(pred: NDArray[np.bool_], truth: NDArray[np.bool_], mask: NDArray[np.bool_] | None = None) -> TagMetrics
    # mask True = excluded pair
DEFAULT_GRID = np.round(np.arange(0.05, 0.96, 0.01), 2)
def best_thresholds(scores: NDArray[np.float32], truth, mask=None, grid=DEFAULT_GRID) -> NDArray[np.float32]
    # per tag argmax F1 over grid; ties -> lowest threshold; tag with no positives -> 0.5
def macro_f1(m: TagMetrics, *, min_support: int) -> float     # mean F1 over tags with support >= min_support; nan if none
def micro_f1(m: TagMetrics) -> float
def roc_auc(scores: NDArray[np.floating], truth: NDArray[np.bool_]) -> float
    # Mann–Whitney with average ranks for ties; nan if either class is empty
```

- [ ] **Step 1: Write the failing tests** — hand-computed cases:

```python
def test_confusion_counts_and_mask():
    # 3 images x 2 tags; mask one pair; assert tp/fp/fn per tag by hand
def test_confusion_zero_predictions_precision_is_zero():
    # tag never predicted, 2 positives -> precision 0.0, recall 0.0, f1 0.0 (no nan)
def test_best_thresholds_picks_max_f1_lowest_on_tie():
    # scores [0.2, 0.6, 0.9], truth [F, T, T] -> any threshold in (0.2, 0.6] gives F1 1 -> 0.21
def test_best_thresholds_tag_without_positives(): -> 0.5
def test_macro_f1_respects_min_support_and_micro_f1()
def test_roc_auc_perfect_random_and_ties():
    # perfect -> 1.0; reversed -> 0.0; all equal scores -> 0.5; single class -> nan
```

- [ ] **Step 2: Run to verify failure; implement; run to PASS; mypy; commit**

```bash
git add app/services/ml_probe/metrics.py tests/services/ml_probe/test_metrics.py
git commit -m "feat(ml-probe): confusion metrics, per-tag thresholds, rank AUC"
```

---

### Task 6: The linear head and its trainer

**Files:**
- Create: `app/services/ml_probe/head.py`
- Test: `tests/services/ml_probe/test_head.py`

**Interfaces:**
```python
@dataclass
class LinearHead:
    weight: NDArray[np.float32]     # [D, K']
    bias: NDArray[np.float32]       # [K']
    mean: NDArray[np.float32]; std: NDArray[np.float32]   # [D]
    tag_ids: NDArray[np.int64]      # [K']
    thresholds: NDArray[np.float32] # [K'], filled with 0.5 until chosen
    meta: dict[str, Any]
    def scores(self, x: NDArray[np.floating]) -> NDArray[np.float32]   # sigmoid(((x - mean) / std) @ W + b)
    def save(self, path: Path) -> None   # np.savez; meta as a JSON string array
    @classmethod def load(cls, path: Path) -> "LinearHead"

def standardization_stats(x, rows: NDArray[np.int64], *, chunk: int = 65_536) -> tuple[NDArray[np.float32], NDArray[np.float32]]
    # mean, std over x[rows] in chunks; std floored at 1e-6

@dataclass
class TrainConfig:
    lr: float = 1e-3; batch_size: int = 4096; l2: float = 1e-4
    max_epochs: int = 20; patience: int = 2; seed: int = 0

def train_linear_head(x, y: NDArray[np.bool_], train_rows, val_rows, *, tag_ids, mean, std,
                      config: TrainConfig, log: Callable[[str], None] = print) -> LinearHead
    # x: any [N, D] indexable (memmap ok); y: [N, K'] aligned with x rows
    # BCE mean over tags, Adam (0.9, 0.999, 1e-8), l2 * W added to the weight gradient,
    # shuffled minibatches (sort each batch's indices before gathering from a memmap),
    # val loss each epoch in chunks, early stop on val loss with patience, return the best epoch's weights;
    # meta records config, epochs run, best val loss
```

- [ ] **Step 1: Write the failing tests**

```python
def test_scores_shape_and_standardization():
    # mean/std applied: head with W = I rows, x == mean -> scores == sigmoid(bias)
def test_save_load_round_trip(tmp_path)
def test_standardization_stats_matches_numpy_on_small_input()
def test_train_recovers_planted_separator():
    rng = np.random.default_rng(0); D, K, N = 16, 3, 6000
    x = rng.normal(size=(N, D)).astype(np.float16); w_true = rng.normal(size=(D, K)); b_true = rng.normal(size=K)
    y = (x.astype(np.float32) @ w_true + b_true) > 0
    train, val = np.arange(0, 5000), np.arange(5000, 6000)
    mean, std = standardization_stats(x, train)
    head = train_linear_head(x, y, train, val, tag_ids=np.arange(K), mean=mean, std=std,
                             config=TrainConfig(max_epochs=30, batch_size=256, lr=1e-2))
    pred = head.scores(x[val].astype(np.float32)) >= 0.5
    m = confusion(pred, y[val]); assert m.f1.min() > 0.95
    assert head.meta["epochs_run"] <= 30 and head.meta["best_val_loss"] < 0.2
```

- [ ] **Step 2: Run to verify failure; implement; run to PASS; mypy; commit**

```bash
git add app/services/ml_probe/head.py tests/services/ml_probe/test_head.py
git commit -m "feat(ml-probe): numpy logistic head with Adam and early stopping"
```

---

### Task 7: Comparison and report

**Files:**
- Create: `app/services/ml_probe/report.py`
- Test: `tests/services/ml_probe/test_report.py`

**Interfaces:**
```python
MIN_SUPPORT = 20; WIN_GAP = 0.05; FIXED_BASELINE_THRESHOLD = 0.35

@dataclass
class View:            # one mask applied to one image slice
    name: str          # "headline" | "sensitivity" | ... and slice "all" | "newest10"
    rows: NDArray[np.int64]            # row indices into the eval set
    mask: NDArray[np.bool_] | None     # [len(rows), K']

@dataclass
class Comparison:
    tag_ids; titles
    per_tag: dict[str, TagMetrics]     # keys "probe", "baseline_fixed", "baseline_best"
    thresholds: dict[str, NDArray[np.float32]]
    support: NDArray[np.int64]
    def winners(self) -> NDArray[np.str_]        # "probe" | "baseline" | "tie" vs baseline_best, gap >= WIN_GAP
    def summary(self) -> dict[str, float]        # macro/micro F1 per system, tags >= 0.5 F1 per system, n tags won

def compare(*, probe_scores, baseline_scores, truth, has_raw, view: View,
            probe_thresholds, baseline_best_thresholds, parent_index) -> Comparison
    # drops rows where ~has_raw; closes both score matrices upward (max) before thresholding
def reviewed_view(*, probe_scores, baseline_scores, provenance, rows) -> dict[str, float]
    # pairs with provenance in {APPROVED, REJECTED} within rows; pooled precision/recall at each
    # system's thresholds and roc_auc per system; counts of approved/rejected pairs
def newest_rows(image_ids_in_eval: NDArray[np.int64], fraction: float = 0.1) -> NDArray[np.int64]
def write_csv(path: Path, comparison: Comparison) -> None
def render_markdown(summaries: dict[str, dict[str, float]], reviewed: dict[str, dict[str, float]]) -> str
```

- [ ] **Step 1: Write the failing tests**

```python
def test_compare_drops_rows_without_raw():
    # 2 images, second has_raw False and is a baseline all-negative; support counts only image 1
def test_winners_use_gap():
    # per-tag F1 probe [0.9, 0.5, 0.52], baseline_best [0.5, 0.9, 0.5] -> ["probe", "baseline", "tie"]
def test_compare_closes_scores_before_thresholding():
    # child predicted 0.9, parent 0.1, threshold 0.5, truth parent T -> parent counts as tp
def test_reviewed_view_counts_and_auc():
    # 3 approved, 1 rejected pairs; probe ranks them perfectly -> auc 1.0, precision at threshold by hand
def test_newest_rows_takes_top_decile_by_id()
def test_write_csv_and_render_markdown_have_expected_columns(tmp_path)
```

- [ ] **Step 2: Run to verify failure; implement; run to PASS; mypy; commit**

```bash
git add app/services/ml_probe/report.py tests/services/ml_probe/test_report.py
git commit -m "feat(ml-probe): probe-vs-mapping comparison under label masks"
```

---

### Task 8: The `ml_probe.py` CLI

**Files:**
- Create: `scripts/ml_probe.py`
- Test: `tests/services/ml_probe/test_cli.py`

**Interfaces:** `uv run python scripts/ml_probe.py <subcommand>`:
- `labels --out labels.npz [--status 1]` → `load_theme_tag_graph` + `build_label_set` → `LabelSet.save`. Prints N, K, counts per provenance code.
- `baseline --labels labels.npz --model swinv2_base_window8_256.dbv4-full --out baseline.npz` → for `fold_of(label_set.image_ids) in {TEST_FOLD, VAL_FOLD}` → `build_baseline_scores` → save.
- `train --embeddings DIR --labels labels.npz --out head.npz [--min-train-positives 100] [--lr --batch-size --l2 --max-epochs --patience --seed]` → align by id (`store.rows_for`; only ids present in both), train rows = fold ≥ 2, val = `VAL_FOLD`; tags with ≥ min positives in train; `standardization_stats`; `train_linear_head`; thresholds = `best_thresholds` on val under the **headline mask** (`approved_mask`); `head.meta` gains `model` (from the store meta), `split` (`"image_id % 10; test 0, val 1"`), `min_train_positives`, `n_train`, `n_val`; save; print K', epochs, best val loss.
- `eval --embeddings DIR --labels labels.npz --baseline baseline.npz --head head.npz --out-dir DIR` → test rows aligned across the three sources; probe scores in chunks; baseline columns restricted to head tag ids; `baseline_best` thresholds from the val fold under the headline mask; views: headline (`approved_mask`), sensitivity (`approved_mask | pending_mask`), each on `all` and `newest10`; `reviewed_view`; writes `per_tag_<view>.csv` and `summary.md`; prints the summary.
- Functions are plain and importable: `cmd_labels(args)`, `cmd_baseline(args)`, `cmd_train(args)`, `cmd_eval(args)`, `build_parser()`.

- [ ] **Step 1: Write the failing end-to-end test (no DB, no model)**

```python
def test_train_and_eval_align_by_image_id(tmp_path):
    # synthetic: store with ids [1..60] minus 7, plus 999 (unlabeled); labels for ids [1..60] (K=3, one parent);
    # provenance so that fold 0/1 images exist, two approved pairs, one pending; baseline.npz for folds 0/1
    # with has_raw False for one image; D=8 vectors generated from planted weights so the probe is learnable
    args = build_parser().parse_args(["train", "--embeddings", ..., "--labels", ..., "--out", ..., "--min-train-positives", "3", "--max-epochs", "30"])
    cmd_train(args)
    head = LinearHead.load(...); assert set(head.tag_ids) <= set(label_set.tag_ids)
    cmd_eval(build_parser().parse_args(["eval", ...]))
    assert (out_dir / "summary.md").exists() and (out_dir / "per_tag_headline_all.csv").exists()
    # summary mentions "headline" and "sensitivity" and "reviewed"
```

- [ ] **Step 2: Run to verify failure; implement the CLI; run to PASS.**

- [ ] **Step 3: Type-check and commit**

```bash
uv run mypy scripts/ml_probe.py app/
git add scripts/ml_probe.py tests/services/ml_probe/test_cli.py
git commit -m "feat(ml-probe): labels/baseline/train/eval CLI"
```

---

### Task 9: Smoke slice on skinny (operational)

**Files:** none in the repo. Work dir `~/ml-backfill/probe-smoke/`.

- [ ] **Step 1: Fresh manifest and a 2% slice**

```bash
cd /home/dtaylor/shuushuu-api && export DATABASE_URL=...   # Global Constraints
uv run python scripts/ml_backfill_manifest.py --out ~/ml-backfill/probe-manifest.jsonl
awk 'NR % 50 == 0' ~/ml-backfill/probe-manifest.jsonl > ~/ml-backfill/probe-smoke/manifest.jsonl
wc -l ~/ml-backfill/probe-smoke/manifest.jsonl    # ≈ 21,000
```

- [ ] **Step 2: Extract on the GPU** (expect 10–25 minutes)

```bash
HSA_OVERRIDE_GFX_VERSION=10.3.0 ~/ml-rocm/bin/python -u scripts/ml_gpu_infer.py \
  --manifest ~/ml-backfill/probe-smoke/manifest.jsonl \
  --embeddings-out ~/ml-backfill/probe-smoke/embeddings \
  --model-dir ~/ml-models/swinv2_base_window8_256.dbv4-full \
  --storage-path /mnt/shuushuu-testing --variant thumbs --prefetch-workers 4 \
  2>&1 | tee ~/ml-backfill/probe-smoke/extract.log
```
Expected: `providers: ['ROCMExecutionProvider', ...]`, a `done:` line, and `embeddings.ids` size ÷ 8 equal to processed count. Record the images/second in the log for the overnight estimate. Kill it after a few hundred rows once and restart to see the resume message.

- [ ] **Step 3: Labels and baseline (full DB; these do not depend on the slice)**

```bash
uv run python scripts/ml_probe.py labels --out ~/ml-backfill/probe/labels.npz
uv run python scripts/ml_probe.py baseline --labels ~/ml-backfill/probe/labels.npz \
  --model swinv2_base_window8_256.dbv4-full --out ~/ml-backfill/probe/baseline.npz
```
Expected: N ≈ 1,057,000; K ≈ 347; APPROVED pairs ≈ 126,500; PENDING ≈ 2.28M. Watch RSS (`/usr/bin/time -v`); it must stay well under 4 GB.

- [ ] **Step 4: Train and evaluate on the slice**

```bash
uv run python scripts/ml_probe.py train --embeddings ~/ml-backfill/probe-smoke/embeddings \
  --labels ~/ml-backfill/probe/labels.npz --out ~/ml-backfill/probe-smoke/head.npz --min-train-positives 20
uv run python scripts/ml_probe.py eval --embeddings ~/ml-backfill/probe-smoke/embeddings \
  --labels ~/ml-backfill/probe/labels.npz --baseline ~/ml-backfill/probe/baseline.npz \
  --head ~/ml-backfill/probe-smoke/head.npz --out-dir ~/ml-backfill/probe-smoke/eval
cat ~/ml-backfill/probe-smoke/eval/summary.md
```
Expected: runs end to end; numbers are noisy at this size and are not the result. Fix anything that breaks here before the overnight run; add a regression test for it.

---

### Task 10: Full extraction, training, evaluation, write-up

- [ ] **Step 1: Overnight extraction** in tmux, resumable (rerun the same command after any crash):

```bash
tmux new -d -s probe-extract "cd /home/dtaylor/shuushuu-api && HSA_OVERRIDE_GFX_VERSION=10.3.0 \
  ~/ml-rocm/bin/python -u scripts/ml_gpu_infer.py --manifest ~/ml-backfill/probe-manifest.jsonl \
  --embeddings-out ~/ml-backfill/probe/embeddings --model-dir ~/ml-models/swinv2_base_window8_256.dbv4-full \
  --storage-path /mnt/shuushuu-testing --variant thumbs --prefetch-workers 4 \
  >> ~/ml-backfill/probe/extract.log 2>&1"
```
Check: `stat -c %s ~/ml-backfill/probe/embeddings/embeddings.ids` ÷ 8 ≈ manifest rows minus missing files.

- [ ] **Step 2: Train and evaluate with the defaults** (`--min-train-positives 100`), then copy `~/ml-backfill/probe/` to `/sakura/shuushuu/ml-backfill/probe/`.

- [ ] **Step 3: Write up.** Put `summary.md` and the per-tag CSVs on `/sakura`, and write the decision as an ADR via the `plan-close-out` skill after the PR merges. The PR description carries the summary table.

- [ ] **Step 4: Finish the branch** — `superpowers:finishing-a-development-branch`; PR title `feat(ml-probe): theme-tag linear probe on frozen swinv2 embeddings`.

## Parallelism

Tasks 3, 4 and 5 share only the interfaces written above and can be built in any order or concurrently once Task 1 exists. Tasks 6 and 7 use `confusion` from Task 5 in their tests. Task 8 needs all of them. Task 9 needs 2, 3, 4 and 8. Task 10 needs 9.

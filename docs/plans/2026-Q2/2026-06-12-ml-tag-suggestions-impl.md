# ML Tag Suggestions Port Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the ML tag suggestion system from the stale `feature/tag-suggestion-system` branch onto current main, fixing the confirmed double-sigmoid bug, removing the production mock fallback, and adapting to conventions that landed since January (UtcDateTime, alembic-built test DB, R2, new worker startup).

**Architecture:** ONNX anime tagger (WD-SwinV2-Tagger-v3, pluggable to animetimm models) runs in the arq worker after upload; predictions map from Danbooru vocabulary to internal tags via a `tag_mappings` table, get alias/hierarchy-resolved, and land as `pending` rows in a new `ml_tag_suggestions` table; users/moderators review via API (approve creates a TagLink). Deliberately separate from the human `image_report_tag_suggestions` flow. Whole feature is gated behind `ML_TAG_SUGGESTIONS_ENABLED` (default off — merging activates nothing).

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB, arq/Redis, onnxruntime ≥1.26.0 (first version with Python 3.14 wheels), numpy, Pillow.

**Source branch:** All ported files come from `feature/tag-suggestion-system`. Read originals with:
`git show feature/tag-suggestion-system:<path>`

**Decisions already made (do not relitigate):**
- v1 suggests **theme (general) tags only**. Character category is excluded from inference — re-enabling later is a one-line change in `MLTagSuggestionService.generate_suggestions` plus character rows in `tag_mappings`. Leave a comment marking the extension point.
- Table/model/API names use the `ml_` prefix (`ml_tag_suggestions`, `MlTagSuggestions`, `/ml-tag-suggestions`) to stay distinct from the report-based human suggestion system.
- Drop from the old branch: `MLModelVersion` model+table (never queried), `model_source` column (only one source exists; `model_version` carries the model name), `external_source` column on tag_mappings (all current vocab is Danbooru), `TagSuggestionStatsResponse`/`SuggestionStatusResponse` schemas (no endpoints use them), `scripts/analyze_tag_distribution.py` (one-off analysis).
- No MockModel anywhere in `app/`. Missing model files raise at load. Tests inject fakes defined under `tests/`.
- Confidence threshold: single source `settings.ML_MIN_CONFIDENCE` (default 0.35, the WD-tagger community default for general tags). The old job's hardcoded 0.6 was calibrated against double-sigmoid values (raw ≈0.4) and is obsolete.
- The job reads image files from local `STORAGE_PATH` (R2 finalize does not delete local files). Missing file is already handled gracefully.

**Verification environment notes:**
- Run tests from the worktree root `.worktrees/ml-tag-suggestions/`.
- Full suite: `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest -n 4 -q` (baseline: 1766 passed, 10 skipped, ~55s).
- Schema sync: `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/integration/test_schema_sync.py --schema-sync -q`
- Real ONNX models exist at `/home/dtaylor/shuu/ml_models/` (not inside the repo). Integration tests that need them must resolve `settings.ML_MODELS_PATH` and **skip** when files are absent.
- alembic head on main is `528091e4fac9` — verify with `uv run alembic heads` before writing the migration.

---

## Chunk 1: Foundation — deps, config, models, migration

### Task 1: Dependencies and settings

**Files:**
- Modify: `pyproject.toml` (dependencies list, around line 39)
- Modify: `app/config.py` (after the Image Processing block, ~line 115)
- Modify: `.env.example`

- [x] **Step 1.1: Add dependencies**

In `pyproject.toml` main `dependencies`, after `"aiosmtplib>=5.0.0",`:

```toml
    "onnxruntime>=1.26.0",  # 1.26.0 is the first release with Python 3.14 wheels
    "numpy>=2.4.1",
```

Run: `uv lock && uv sync` — expect success (cp314 wheels exist for both).

- [x] **Step 1.2: Add settings**

In `app/config.py`, after the Image Processing block:

```python
    # ML Tag Suggestions
    ML_TAG_SUGGESTIONS_ENABLED: bool = Field(
        default=False,
        description=(
            "Master switch for ML tag suggestions. When true the arq worker "
            "loads the ONNX model at startup (and fails to start if model "
            "files are missing), uploads enqueue generation jobs, and the "
            "generate endpoint is available."
        ),
    )
    ML_MODELS_PATH: str = Field(
        default="ml_models",
        description="Directory holding ONNX model subdirectories; relative paths resolve against the project root",
    )
    ML_MODEL_NAME: str = Field(
        default="wd-swinv2-tagger-v3",
        description="Model subdirectory to load: wd-swinv2-tagger-v3 or an animetimm name like swinv2_base_window8_256.dbv4-full",
    )
    ML_MIN_CONFIDENCE: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Minimum model probability for a prediction to become a suggestion",
    )
```

- [x] **Step 1.3: Document in .env.example**

Append to `.env.example` (match its existing comment style):

```
# ML tag suggestions (off by default; needs ONNX model files — see docs/ml-tag-suggestions.md)
ML_TAG_SUGGESTIONS_ENABLED=false
ML_MODELS_PATH=ml_models
ML_MODEL_NAME=wd-swinv2-tagger-v3
ML_MIN_CONFIDENCE=0.35
```

- [x] **Step 1.4: Sanity check** — `uv run python -c "from app.config import settings; print(settings.ML_TAG_SUGGESTIONS_ENABLED)"` → `False`

- [x] **Step 1.5: Commit** — `feat(ml-suggestions): add onnxruntime deps and feature-flagged settings`

### Task 2: Models

**Files:**
- Create: `app/models/ml_tag_suggestion.py`
- Create: `app/models/tag_mapping.py`
- Modify: `app/models/__init__.py`
- Test: `tests/models/test_ml_tag_suggestion.py`, `tests/models/test_tag_mapping.py`

Reference originals: `git show feature/tag-suggestion-system:app/models/tag_suggestion.py` and `...:app/models/tag_mapping.py`, plus branch tests `tests/models/test_tag_suggestion.py` / `tests/models/test_tag_mapping.py`. Follow main's conventions from `app/models/image_report_tag_suggestion.py` (Base + table classes, explicit `sa_column` FKs, `UtcDateTime`).

- [x] **Step 2.1: Write failing model tests**

`tests/models/` does **not** exist on main — create the directory with an empty `tests/models/__init__.py` (every test package has one). Port the branch's two model test files, renamed and adapted: class `MlTagSuggestions` (no `model_source` field — assert `model_version` instead), class `TagMappings` (no `external_source`). Keep the real-DB insert/query/constraint tests; drop any `MLModelVersion` tests. Drop the branch's `@pytest.mark.asyncio` markers (main runs `asyncio_mode = "auto"`). Parent `Images`/`Tags`/`Users` rows come from existing conftest fixtures (`db_session`, `test_user`, `test_tag`, `test_image` — all in `tests/conftest.py`).

- [x] **Step 2.2: Run to confirm failure** — `uv run pytest tests/models/ -q` → ImportError (module doesn't exist).

- [x] **Step 2.3: Write `app/models/ml_tag_suggestion.py`**

```python
"""
SQLModel-based MlTagSuggestions model.

Stores ML-generated tag suggestions awaiting human review. Distinct from
ImageReportTagSuggestions, which stores human suggestions filed via reports.

Lifecycle: pipeline inserts status='pending' → reviewer approves (TagLink
created) or rejects. Regeneration resets approved rows to pending when their
tag has been removed from the image.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint, text
from sqlalchemy import Enum as SQLEnum
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class MlTagSuggestionBase(SQLModel):
    """Shared public fields for ML tag suggestions."""

    image_id: int
    tag_id: int
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str = Field(max_length=100)  # e.g. "wd-swinv2-tagger-v3"


class MlTagSuggestions(MlTagSuggestionBase, table=True):
    """Database table for ML-generated tag suggestions."""

    __tablename__ = "ml_tag_suggestions"

    __table_args__ = (
        UniqueConstraint("image_id", "tag_id", name="unique_ml_suggestion_image_tag"),
        Index("idx_ml_suggestion_status", "status"),
    )

    suggestion_id: int | None = Field(default=None, primary_key=True)

    image_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        )
    )
    status: str = Field(
        default="pending",
        sa_column=Column(
            SQLEnum("pending", "approved", "rejected", name="ml_suggestion_status"),
            nullable=False,
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
    reviewed_at: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    reviewed_by_user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
```

- [x] **Step 2.4: Write `app/models/tag_mapping.py`**

```python
"""
SQLModel-based TagMappings model.

Maps external (Danbooru-vocabulary) tag names emitted by ML taggers to
internal tag IDs. A row with internal_tag_id=NULL means "known but ignored"
(e.g. '1girl'); an absent row means unmapped (logged, then dropped).
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class TagMappingBase(SQLModel):
    """Shared public fields for tag mappings."""

    external_tag: str = Field(max_length=255)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TagMappings(TagMappingBase, table=True):
    """Database table mapping external tagger vocabulary to internal tags."""

    __tablename__ = "tag_mappings"

    __table_args__ = (UniqueConstraint("external_tag", name="unique_external_tag"),)

    mapping_id: int | None = Field(default=None, primary_key=True)

    internal_tag_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("tags.tag_id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(UtcDateTime, nullable=True, server_default=text("current_timestamp()")),
    )
```

- [x] **Step 2.5: Register in `app/models/__init__.py`** — add imports `from app.models.ml_tag_suggestion import MlTagSuggestions` and `from app.models.tag_mapping import TagMappings` in the alphabetical positions used by the file, plus both names in `__all__`.

- [x] **Step 2.6: Migration**

Verify head: `uv run alembic heads` → `528091e4fac9`. Create `uv run alembic revision -m "add ml tag suggestion tables"`, then edit (see `docs/creating_alembic_migrations.md`; follow `e66f8043bc60` as the style reference):

```python
def upgrade() -> None:
    """Create ml_tag_suggestions and tag_mappings tables."""
    op.create_table(
        "tag_mappings",
        sa.Column("mapping_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_tag", sa.String(length=255), nullable=False),
        sa.Column("internal_tag_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["internal_tag_id"],
            ["tags.tag_id"],
            name="fk_tag_mappings_internal_tag_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mapping_id"),
        sa.UniqueConstraint("external_tag", name="unique_external_tag"),
    )

    op.create_table(
        "ml_tag_suggestions",
        sa.Column("suggestion_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="ml_suggestion_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("current_timestamp()"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            name="fk_ml_tag_suggestions_image_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            name="fk_ml_tag_suggestions_tag_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.user_id"],
            name="fk_ml_tag_suggestions_reviewed_by_user_id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("suggestion_id"),
        sa.UniqueConstraint("image_id", "tag_id", name="unique_ml_suggestion_image_tag"),
    )
    op.create_index("idx_ml_suggestion_status", "ml_tag_suggestions", ["status"])


def downgrade() -> None:
    """Drop ml_tag_suggestions and tag_mappings tables."""
    op.drop_table("ml_tag_suggestions")
    op.drop_table("tag_mappings")
```

FK names follow the `fk_<table>_<column>` convention enforced by `tests/integration/test_fk_constraint_names.py` (it inspects the migrated DB, so naming them in the migration is what matters; the model classes use bare `ForeignKey(...)` like the rest of main's models).

- [x] **Step 2.7: Apply and verify** — model tests pass (`uv run pytest tests/models/ -q`; new head forces the conftest full-rebuild path), FK names pass (`uv run pytest tests/integration/test_fk_constraint_names.py -q`), and schema sync runs clean (`MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/integration/test_schema_sync.py --schema-sync -q`). Note: schema-sync proves `create_all` and alembic both build without FK/type errors (catches signedness mismatches), but its deep column comparisons cover a hardcoded legacy-table list — don't read green as full model↔migration parity; the embedded model/DDL above were cross-checked by review instead.

- [x] **Step 2.8: Commit** — `feat(ml-suggestions): add MlTagSuggestions and TagMappings models with migration`

---

## Chunk 2: ML inference layer

### Task 3: ONNX model wrappers + model assets (sigmoid fix)

**Files:**
- Create: `app/services/onnx_model.py` (port, with fix)
- Create: `app/services/animetimm_model.py` (port as-is)
- Create: `ml_models/wd-swinv2-tagger-v3/README.md` (port, with contract fix)
- Create: `ml_models/wd-swinv2-tagger-v3/selected_tags.csv` (port as-is, 10,862 lines)
- Modify: `.gitignore`
- Test: `tests/services/test_onnx_model.py` (new), `tests/integration/test_wd_tagger_model.py` (new)

**The bug being fixed:** `_predict_sync` in the old branch applied `1/(1+exp(-x))` to model output, but the WD-tagger v3 ONNX graph already ends in sigmoid — output is probabilities. Verified empirically 2026-06-12: raw output range [0.0000, 0.9654] on a real image, all 10,861 values in [0,1]; 24 tags exceed 0.35 raw, while sigmoid(raw) puts **all 10,861** above 0.35.

- [x] **Step 3.1: Write failing preprocessing unit tests** (`tests/services/test_onnx_model.py`, marked `unit`; no model file needed — `_preprocess_image` works on an unloaded instance). Generate a small RGBA PNG with Pillow in tmp_path; assert output shape `(1, 448, 448, 3)`, dtype float32, value range [0, 255], and BGR order (make the test image solid red; assert channel 0 ≈ 0 and channel 2 ≈ 255 at some pixel).

- [x] **Step 3.2: Run to confirm failure** — ImportError.

- [x] **Step 3.3: Port the wrappers.**

```bash
mkdir -p ml_models/wd-swinv2-tagger-v3
git show feature/tag-suggestion-system:app/services/onnx_model.py > app/services/onnx_model.py
git show feature/tag-suggestion-system:app/services/animetimm_model.py > app/services/animetimm_model.py
git show feature/tag-suggestion-system:ml_models/wd-swinv2-tagger-v3/README.md > ml_models/wd-swinv2-tagger-v3/README.md
git show feature/tag-suggestion-system:ml_models/wd-swinv2-tagger-v3/selected_tags.csv > ml_models/wd-swinv2-tagger-v3/selected_tags.csv
```

Edits to `onnx_model.py` `_predict_sync`:

```python
        # Run inference. The WD-Tagger v3 ONNX graph ends in sigmoid, so the
        # output is already per-tag probabilities in [0, 1] — do NOT apply
        # sigmoid again (doing so compresses everything into [0.5, 0.73]).
        output_name = self.session.get_outputs()[0].name
        outputs = self.session.run([output_name], {self.input_name: img_array})
        probabilities = outputs[0][0]  # Remove batch dimension
```

(delete the `raw_predictions` line and the two sigmoid lines; the loop below already consumes `probabilities`). Also update `README.md`: `- **Output**: 10,861 tag probabilities (sigmoid already applied in the ONNX graph — threshold directly)`.

`animetimm_model.py` ports unchanged (it already uses the post-sigmoid `prediction` output).

- [x] **Step 3.4: gitignore** — append the branch's block:

```
# ML models (large binary files - download separately)
ml_models/*.onnx
ml_models/**/*.onnx
```

- [x] **Step 3.5: Write the real-model integration test** (`tests/integration/test_wd_tagger_model.py`). Resolve model dir the same way `ml_service` does (settings.ML_MODELS_PATH relative to project root); `pytest.mark.skipif` when `model.onnx` is absent. Load `WDTaggerModel`, predict on a Pillow-generated 600×400 RGB image, assert: every confidence in [0,1]; `len(results with min_confidence=0.35, all categories) < 2000` (the double-sigmoid tripwire — under the bug all 10,861 tags pass 0.35); and with `min_confidence=0.0` the max confidence exceeds 0.3 (guards against degenerate all-zeros output from broken preprocessing). Capture log output expectations per repo test rules (model-load INFO lines are expected).

- [x] **Step 3.6: Run** — unit tests pass; integration test passes locally if user has set `ML_MODELS_PATH=/home/dtaylor/shuu/ml_models` in `.env`, else skips. Run it once with the env var pointed at the real models to prove the fix: `ML_MODELS_PATH=/home/dtaylor/shuu/ml_models uv run pytest tests/integration/test_wd_tagger_model.py -q` → pass (not skip).

- [x] **Step 3.7: Commit** — `feat(ml-suggestions): port ONNX tagger wrappers, fixing double-sigmoid on WD-tagger output`

### Task 4: MLTagSuggestionService (no mock, fail-fast, themes only)

**Files:**
- Create: `app/services/ml_service.py` (rewrite of the branch version)
- Test: `tests/services/test_ml_service.py` (new; do not port the branch one — it tested MockModel behavior)

- [x] **Step 4.1: Write failing tests.** Define a `FakeTaggingModel` in the test file (satisfies the `TaggingModel` protocol, returns canned predictions, records the `include_categories` it was called with). Inject by assigning `service.model = fake` after construction — that attribute is the service's only seam; don't invent a constructor parameter. Tests:
  1. `load_models` raises `FileNotFoundError` naming the expected path when model files are absent (point `ML_MODELS_PATH` at tmp_path via monkeypatch of settings).
  2. `load_models` raises `ValueError` for an unrecognized `ML_MODEL_NAME`.
  3. `generate_suggestions` with an injected fake model passes `include_categories={GENERAL_CATEGORY}` only (the v1 themes-only decision) and returns dicts with keys `external_tag`, `confidence`, `model_version` (no `model_source`).
  4. `generate_suggestions` before any model is set raises `RuntimeError`.

  These exercise real service logic (path resolution, category selection, output shaping) against a protocol fake at the inference boundary — not mocked behavior.

- [x] **Step 4.2: Run to confirm failure.**

- [x] **Step 4.3: Implement.** Start from the branch file and apply:
  - Delete `MockModel` and `_using_mock` entirely (this orphans the `asyncio` and `random` imports — remove them too so the commit is ruff-clean).
  - `_load_wd_tagger` / `_load_animetimm`: when files are missing, `raise FileNotFoundError(f"ML model files not found: {model_path} (set ML_MODELS_PATH or download the model — see ml_models/wd-swinv2-tagger-v3/README.md)")`.
  - Unknown `ML_MODEL_NAME`: `raise ValueError(...)` instead of mock fallback.
  - `generate_suggestions`: categories = `{ANIMETIMM_GENERAL}` or `{GENERAL_CATEGORY}` only, with comment: `# v1 suggests theme tags only. To enable character suggestions later, add CHARACTER_CATEGORY here and populate character rows in tag_mappings.` Output dicts: `external_tag`, `confidence`, `model_version` (= `self._model_name`). Drop `model_source`.
  - Keep the `TaggingModel` protocol, project-root path resolution, logging, `cleanup()`.

- [x] **Step 4.4: Run** — `uv run pytest tests/services/test_ml_service.py -q` → pass.

- [x] **Step 4.5: Commit** — `feat(ml-suggestions): ML service with fail-fast loading, themes-only inference`

### Task 5: Mapping and resolver services

**Files:**
- Create: `app/services/tag_mapping_service.py` (port, adapt to TagMappings)
- Create: `app/services/tag_resolver.py` (port, clean comments)
- Test: `tests/services/test_tag_mapping_service.py`, `tests/services/test_tag_resolver.py` (port branch tests, adapt)

- [x] **Step 5.1: Port branch tests**, adapted: model import becomes `TagMappings`, no `external_source` field anywhere, `model_source` keys dropped from suggestion dicts (use `model_version`). **Fixture bug in the branch tests:** `tests/services/test_tag_resolver.py` constructs alias tags as `Tags(..., alias=46)` (3 occurrences) but the field is `alias_of` — SQLModel silently drops the unknown kwarg, so two tests fail against even a correct resolver. Fix to `alias_of=` while porting. These run against the real test DB.
- [x] **Step 5.2: Run to confirm failure** (ImportError).
- [x] **Step 5.3: Port implementations.** `tag_mapping_service.py`: drop the `external_source == "danbooru"` filter (column is gone); propagate `model_version` instead of `model_source` in resolved dicts; update the docstring Args/Returns lists that still mention `model_source`/`external_source`. `tag_resolver.py`: port as-is but delete the `# CRITICAL FIX #N` / `# IMPORTANT FIX #N` review-artifact comments (keep the explanatory content where it states real constraints, e.g. batch-loading to avoid N+1) and fix its docstring's `model_source` key list.
- [x] **Step 5.4: Run** — both test files pass.
- [x] **Step 5.5: Commit** — `feat(ml-suggestions): port tag mapping and alias/hierarchy resolution services`

---

## Chunk 3: Pipeline, worker, API, upload, ops

### Task 6: Shared suggestion pipeline + arq job + worker wiring

**Files:**
- Create: `app/services/ml_suggestion_pipeline.py`
- Create: `app/tasks/ml_tag_suggestion_job.py`
- Modify: `app/tasks/worker.py`
- Test: `tests/services/test_ml_suggestion_pipeline.py`, `tests/tasks/test_ml_tag_suggestion_job.py`, `tests/test_worker.py`

The branch duplicated the generation pipeline between `tag_suggestion_job.py` and the API's `_generate_suggestions_sync`. Extract once; both call it.

- [x] **Step 6.1: Write failing pipeline tests** (`tests/services/test_ml_suggestion_pipeline.py`). `tests/tasks/` does not exist on main — create it with an empty `tests/tasks/__init__.py`. Port the logic-heavy tests from branch `tests/tasks/test_tag_suggestion_job.py` (redundancy filtering: ancestor chains, substring titles; skip-existing-tag; skip-existing-suggestion; reset-approved-when-tag-removed; threshold filtering), driving `generate_and_store_suggestions(db, image, ml_service)` with a fake `MLTagSuggestionService` (canned `generate_suggestions` return) and real DB rows. Image file existence: pipeline takes the resolved path from the image row — tests create a real temp file and monkeypatch `settings.STORAGE_PATH`.

- [x] **Step 6.2: Run to confirm failure.**

- [x] **Step 6.3: Implement `app/services/ml_suggestion_pipeline.py`.** Move from the branch job file: `filter_redundant_suggestions` (unchanged except comment cleanup) and the body of steps 3–9 of `generate_tag_suggestions` as:

```python
async def generate_and_store_suggestions(
    db: AsyncSession,
    image: Images,
    ml_service: MLTagSuggestionService,
) -> int:
    """
    Run ML inference for an image and store pending MlTagSuggestions rows.

    Pipeline: predict (external tags) → map to internal tag IDs → resolve
    aliases/hierarchy → drop redundant vs existing tags → upsert suggestions.
    Existing suggestions are kept (idempotent regeneration); approved
    suggestions whose tag was since removed from the image reset to pending.

    Returns the number of new suggestions created.
    Raises FileNotFoundError if the local image file is missing.
    """
```

Uses `settings.ML_MIN_CONFIDENCE` (not a hardcoded 0.6) when calling `ml_service.generate_suggestions` and when double-checking before insert. Creates `MlTagSuggestions(..., model_version=pred["model_version"], status="pending")`. Commits at the end (same transactional shape as the branch job).

- [x] **Step 6.4: Implement `app/tasks/ml_tag_suggestion_job.py`** — thin arq wrapper:

```python
async def generate_ml_tag_suggestions(ctx: dict[str, Any], image_id: int) -> dict[str, str | int]:
```

Binds log context, opens a session, loads the image (error result if absent), pulls `ml_service = ctx.get("ml_service")` — if `None`, log error and return `{"status": "error", "error": "ML tag suggestions not initialized (ML_TAG_SUGGESTIONS_ENABLED is off or model failed to load)"}` — calls the pipeline, catches exceptions into an error-result dict exactly like other jobs in `app/tasks/` (don't crash the queue).

- [x] **Step 6.5: Wire `app/tasks/worker.py`:**
  - In `startup`, after the meilisearch block:

```python
    # Load the ML tagging model once per worker when the feature is enabled.
    # Deliberately NOT wrapped in try/except: if the flag is on but model
    # files are absent, the worker must fail to start rather than silently
    # skip suggestion generation (no mock fallback by design).
    if settings.ML_TAG_SUGGESTIONS_ENABLED:
        from app.services.ml_service import MLTagSuggestionService

        ml_service = MLTagSuggestionService()
        await ml_service.load_models()
        ctx["ml_service"] = ml_service
        logger.info("ml_service_initialized", model_name=ml_service.model_name)
```

  - In `shutdown`: `if "ml_service" in ctx: await ctx["ml_service"].cleanup()`.
  - Add `func(generate_ml_tag_suggestions, max_tries=3)` to `functions` + import.

- [x] **Step 6.6: Job/worker tests.** `tests/tasks/test_ml_tag_suggestion_job.py`: missing-image → error dict; missing ml_service in ctx → error dict mentioning the flag; happy path with fake service → rows created (reuse pipeline fixtures). Main has no `tests/test_worker.py` — port the branch's, adapted: assert `generate_ml_tag_suggestions` is registered; assert startup with flag off does not set `ctx["ml_service"]`; assert startup with flag on and missing files raises `FileNotFoundError`. (Main's startup also initializes meilisearch — the branch's worker tests predate that; make sure ported startup tests tolerate it, e.g. unreachable meilisearch logs a warning and continues.)

- [x] **Step 6.7: Run all new tests + `uv run pytest tests/tasks tests/services -q`** → pass.

- [x] **Step 6.8: Commit** — `feat(ml-suggestions): shared generation pipeline, arq job, gated worker startup`

### Task 7: Schemas + API endpoints

**Files:**
- Create: `app/schemas/ml_tag_suggestion.py`
- Create: `app/api/v1/ml_tag_suggestions.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/api/v1/test_ml_tag_suggestions.py`

- [x] **Step 7.1: Port API tests** from branch `tests/api/v1/test_tag_suggestions.py` (900 lines), adapted: URL prefix `/api/v1/images/{id}/ml-tag-suggestions`; `MlTagSuggestions` model; no `model_source` (assert `model_version`); generate endpoint returns **503 when `ML_TAG_SUGGESTIONS_ENABLED` is false** (new test); sync-mode tests patch the module-level service getter with a fake (same boundary as Task 6). For stubbing `enqueue_job`, mirror main's current pattern in `tests/api/v1/test_upload.py` (`patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock)` — upload tests moved there since January; `test_images.py` no longer touches enqueue).

- [x] **Step 7.2: Run to confirm failure.**

- [x] **Step 7.3: Port schemas** (`git show feature/tag-suggestion-system:app/schemas/tag_suggestion.py`, then edit): keep `TagSuggestionResponse`→`MlTagSuggestionResponse`, `TagSuggestionsListResponse`→`MlTagSuggestionsListResponse`, `ReviewSuggestionRequest/ReviewSuggestionsRequest/ReviewSuggestionsResponse`, `GenerateSuggestionsResponse`. Replace `model_source: Literal[...]` with `model_version: str`. Delete `TagSuggestionStatsResponse` and `SuggestionStatusResponse`. Update json_schema_extra examples accordingly.

- [x] **Step 7.4: Port the API router** (`git show feature/tag-suggestion-system:app/api/v1/tag_suggestions.py` → `app/api/v1/ml_tag_suggestions.py`) with edits:
  - Paths: `/{image_id}/ml-tag-suggestions`, `.../review`, `.../generate`; router `tags=["ml-tag-suggestions"]`.
  - Auth: use main's idiom — `current_user: CurrentUser` (no `= ...` placeholder hack; `CurrentUser` is an Annotated Depends type on main).
  - Model/schema renames throughout; `model_version` replaces `model_source` in response building.
  - Generate endpoint: first check `if not settings.ML_TAG_SUGGESTIONS_ENABLED: raise HTTPException(status_code=503, detail="ML tag suggestions are disabled")`. Both sync and async modes stay (sync was a deliberate prior decision, ~0.6s inference).
  - Replace `_generate_suggestions_sync` entirely with a call to `generate_and_store_suggestions(db, image, await _get_ml_service())`, translating `FileNotFoundError` to 404. Keep the lazy module-level `_ml_service` singleton for sync mode (the API process loads the model on first sync call only), but drop the `using_mock=_ml_service.using_mock` kwarg from its log call — that property no longer exists after Task 4 (tests patch the getter, so only mypy/runtime would catch it).
  - Drop the old file's imports of `MIN_CONFIDENCE_THRESHOLD` and `filter_redundant_suggestions` from the job module — the pipeline call replaces all of that; import only what the slimmed file uses.
  - Enqueue uses job name `generate_ml_tag_suggestions`.
  - Trim the docstrings' broken artifacts (the old file has mangled examples like `GET / api / v1 / ...` — fix spacing).
  - Register in `app/api/v1/__init__.py` (import + `router.include_router(ml_tag_suggestions.router)`) in the file's existing order.

- [x] **Step 7.5: Run** — `uv run pytest tests/api/v1/test_ml_tag_suggestions.py -q` → pass.

- [x] **Step 7.6: Commit** — `feat(ml-suggestions): API endpoints for listing, reviewing, and generating suggestions`

### Task 8: Upload integration

**Files:**
- Modify: `app/api/v1/images.py` (after the R2 finalize enqueue, ~line 2760)
- Test: extend `tests/api/v1/test_upload.py` (upload tests live here on current main, not `test_images.py` where the branch put them)

- [x] **Step 8.1: Write failing tests** (adapt the branch's upload-test additions into `test_upload.py`): with flag on, upload enqueues `generate_ml_tag_suggestions` with `_defer_by=30.0`; with flag off, it doesn't; enqueue failure doesn't fail the upload.

- [x] **Step 8.2: Implement** after the `r2_finalize_upload_job` block:

```python
        if settings.ML_TAG_SUGGESTIONS_ENABLED:
            # Defer so thumbnail/variant processing finishes first; failure
            # must never fail the upload itself.
            try:
                await enqueue_job(
                    "generate_ml_tag_suggestions",
                    image_id=image_id,
                    _defer_by=30.0,
                )
                logger.debug("ml_tag_suggestion_job_enqueued", image_id=image_id)
            except Exception as e:
                logger.error(
                    "ml_tag_suggestion_enqueue_failed",
                    image_id=image_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
```

- [x] **Step 8.3: Run** — new tests + full `tests/api/v1/test_upload.py` pass.
- [x] **Step 8.4: Commit** — `feat(ml-suggestions): enqueue suggestion generation on upload behind flag`

### Task 9: Data, scripts, compose, docs, licenses

**Files:**
- Create: `data/tag_mappings.csv` (port as-is)
- Create: `scripts/generate_tag_mappings.py`, `scripts/import_tag_mappings.py` (port, adapt)
- Create: `THIRD_PARTY_LICENSES.md` (port as-is — iqdb-rs, animetimm GPL-3.0, WD-tagger Apache-2.0)
- Create: `docs/ml-tag-suggestions.md`
- Modify: `docker-compose.yml`

- [x] **Step 9.1: Port data + scripts.** `import_tag_mappings.py`: adapt to `TagMappings` (no `external_source`). `generate_tag_mappings.py`: port as-is (writes a draft CSV for manual review; only handles category 0 — fine for themes-only v1). Run `uv run python -c "import ast; ast.parse(open('scripts/import_tag_mappings.py').read())"`-level sanity via ruff/mypy in Task 10 (scripts have no test DB seed data to run against here).
- [x] **Step 9.2: docker-compose** — add `- ./ml_models:/app/ml_models:ro` to both the `api` and `arq-worker` service volumes (the worker service was renamed since the branch diff).
- [x] **Step 9.3: Write `docs/ml-tag-suggestions.md`** — condensed from the branch's `docs/tag-suggestion-workflow.md`, updated: flag-gated setup steps (download model per `ml_models/.../README.md`, set env vars, run `import_tag_mappings.py`), endpoint paths, threshold semantics (true probabilities now; 0.35 default), the themes-only v1 scope and the documented character extension point, separation from report-based human suggestions.
- [x] **Step 9.4: Commit** — `feat(ml-suggestions): mapping data, import scripts, compose mounts, docs, licenses`

### Task 10: Integration tests + full verification

**Files:**
- Create: `tests/integration/test_ml_tag_suggestion_workflow.py` (port from branch `test_tag_suggestion_workflow.py`, adapted)

- [x] **Step 10.1: Port the end-to-end workflow test** (fake ML service boundary, real DB + API): generate → suggestions stored → review approve/reject → TagLinks created → regenerate resets removed-tag approvals. Adapt names/paths/flag as in earlier tasks.
- [x] **Step 10.2: Full suite** — `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest -n 4 -q` → 0 failures (1861 passed, 13 skipped).
- [x] **Step 10.3: Schema sync** — `MYSQL_ROOT_PASSWORD=dev_root_password uv run pytest tests/integration/test_schema_sync.py --schema-sync -q` → pass.
- [x] **Step 10.4: Types + lint** — `uv run mypy app/` and `uv run ruff check . && uv run ruff format --check .` → clean.
- [x] **Step 10.5: Real-model smoke (optional but do it — models exist locally)** — `ML_MODELS_PATH=/home/dtaylor/shuu/ml_models uv run pytest tests/integration/test_wd_tagger_model.py -q -m ""` → pass, not skip (3 passed).
- [x] **Step 10.6: Commit any stragglers** — `test(ml-suggestions): end-to-end workflow coverage`

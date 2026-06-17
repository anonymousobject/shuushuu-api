# ML Raw Predictions Store + Re-map — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist raw per-image ML predictions (general + character) in a queryable store so expanding `tag_mappings` re-surfaces suggestions via a cheap **re-map** (no re-inference), and capture the populate pass with the caformer model.

**Architecture:** Three new tables (`ml_external_tags`, `ml_models` dictionaries + `ml_raw_predictions`). The GPU populate pass uses `scripts/ml_gpu_infer.py` (standalone, app-free) extended to capture general+character raw predictions (now with `category`); `ml_backfill_infer.py` is the app-coupled CPU twin and is not used for the GPU populate. A new app-side `generate_raw_predictions` service method supports a future live/CPU raw-capture path. `ml_raw_ingest` bulk-loads the JSONL into the tables. The prediction→implied-set computation is **extracted from `store_predictions` into a shared helper**; `ml_remap` reads raw preds from the table, runs that helper, then applies its **own** reconcile (regenerate pending scoped to `model_version`, preserve reviewed, never re-suggest a dismissed tag).

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (async), Alembic, MariaDB, pytest (real test DB, no mocks), onnxruntime.

**Spec:** `docs/plans/2026-06-17-ml-raw-predictions-store-design.md`

---

## Conventions (read once)

- **Models** use the base+`table=True` pattern with `sa_column=Column(...)`; FK columns use plain `Integer`/`SmallInteger` in the model — **the migration declares the unsigned dialect type** (`mysql.INTEGER(unsigned=True)` / `mysql.SMALLINT(unsigned=True)`) to match the referenced PKs. (Precedent: `alembic/versions/31b4f18cfd81_*.py`.)
- **Tests** need a running MariaDB; they use the `db_session` fixture (transaction-rollback isolation) and seed via `db_session.add_all(...)` / `flush()` / `commit()`. Run one test: `uv run pytest <path>::<name> -v`. Run a file: `uv run pytest tests/services/test_x.py -v`.
- **Standalone scripts** get a session via `async with get_async_session() as db:` (`from app.core.database import get_async_session`).
- Commit after each green task. Pre-commit runs ruff/format on staged files.

---

## Chunk 1: Schema

### Task 1: Models for the three tables

**Files:**
- Create: `app/models/ml_raw_prediction.py`
- Modify: `app/models/__init__.py` (register + `__all__`)
- Test: `tests/models/test_ml_raw_prediction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/models/test_ml_raw_prediction.py
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions


def test_model_tablenames_and_fields():
    """Pure in-memory model test — no DB. The DB round-trip is in Task 2 (needs the
    migration: the test DB builds its schema from the Alembic chain, not metadata)."""
    assert MlExternalTags.__tablename__ == "ml_external_tags"
    assert MlModels.__tablename__ == "ml_models"
    assert MlRawPredictions.__tablename__ == "ml_raw_predictions"
    row = MlRawPredictions(image_id=1, model_id=2, external_tag_id=3, confidence=0.97)
    assert (row.image_id, row.model_id, row.external_tag_id) == (1, 2, 3)
    assert abs(row.confidence - 0.97) < 1e-6
```

- [ ] **Step 2: Run it — fails (module/classes don't exist)**

Run: `uv run pytest tests/models/test_ml_raw_prediction.py -v`
Expected: ImportError / no module `app.models.ml_raw_prediction`.

- [ ] **Step 3: Create the models**

```python
# app/models/ml_raw_prediction.py
"""
Raw ML prediction store.

Persists the model's raw per-image predictions (external Danbooru-vocabulary
tags + confidence) so that changing tag_mappings can re-surface suggestions via
a cheap re-map instead of full re-inference. ml_external_tags and ml_models are
small dictionaries; ml_raw_predictions is the large fact table.
"""

from sqlalchemy import Column, ForeignKey, Integer, SmallInteger, UniqueConstraint
from sqlmodel import Field, SQLModel


class MlExternalTags(SQLModel, table=True):
    """Dictionary of the model vocabulary: external tag name + its category."""

    __tablename__ = "ml_external_tags"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_external_tag_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    category: int = Field(sa_column=Column(SmallInteger, nullable=False))


class MlModels(SQLModel, table=True):
    """Dictionary of ML model versions (e.g. caformer_b36.dbv4-full)."""

    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("name", name="unique_ml_model_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)


class MlRawPredictions(SQLModel, table=True):
    """One row per (image, model, predicted external tag). Composite PK."""

    __tablename__ = "ml_raw_predictions"

    image_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("images.image_id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    model_id: int = Field(
        sa_column=Column(
            SmallInteger,
            ForeignKey("ml_models.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    external_tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("ml_external_tags.id", ondelete="CASCADE", onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    confidence: float = Field(nullable=False)
```

- [ ] **Step 4: Register the models** in `app/models/__init__.py` — add alongside the other model imports and to `__all__`:

```python
from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
# ... add to __all__: "MlExternalTags", "MlModels", "MlRawPredictions"
```

- [ ] **Step 5: Run the test — passes**

Run: `uv run pytest tests/models/test_ml_raw_prediction.py -v`
Expected: PASS (pure in-memory model test — no DB; the DB round-trip is validated in Task 2 after the migration creates the tables).

- [ ] **Step 6: Commit**

```bash
git add app/models/ml_raw_prediction.py app/models/__init__.py tests/models/test_ml_raw_prediction.py
git commit -m "feat(ml): models for raw-prediction store (external tags, models, raw predictions)"
```

### Task 2: Alembic migration

**Files:**
- Create: `alembic/versions/<rev>_add_ml_raw_prediction_tables.py`

- [ ] **Step 1: Find the current head**

Run: `uv run alembic heads`
Note the revision id — use it as `down_revision`.

- [ ] **Step 2: Create the migration** (generate the skeleton, then replace `upgrade`/`downgrade`)

Run: `uv run alembic revision -m "add ml raw prediction tables"`
Then set the body (mirroring `31b4f18cfd81`'s style; **unsigned dialect types** so FKs match):

```python
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from alembic import op

# revision / down_revision set by the generator; down_revision = current head from Step 1

def upgrade() -> None:
    op.create_table(
        "ml_models",
        sa.Column("id", mysql.SMALLINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="unique_ml_model_name"),
    )
    op.create_table(
        "ml_external_tags",
        sa.Column("id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="unique_ml_external_tag_name"),
    )
    op.create_table(
        "ml_raw_predictions",
        sa.Column("image_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("model_id", mysql.SMALLINT(unsigned=True), nullable=False),
        sa.Column("external_tag_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["images.image_id"],
            name="fk_ml_raw_pred_image_id", ondelete="CASCADE", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["ml_models.id"],
            name="fk_ml_raw_pred_model_id", ondelete="CASCADE", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["external_tag_id"], ["ml_external_tags.id"],
            name="fk_ml_raw_pred_external_tag_id", ondelete="CASCADE", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("image_id", "model_id", "external_tag_id"),
    )


def downgrade() -> None:
    op.drop_table("ml_raw_predictions")
    op.drop_table("ml_external_tags")
    op.drop_table("ml_models")
```

- [ ] **Step 3: Apply + verify round-trips**

Run: `uv run alembic upgrade head` then `uv run alembic downgrade -1` then `uv run alembic upgrade head`
Expected: all succeed (no FK type-mismatch errors — that's what the unsigned dialect types guard).

- [ ] **Step 4: Add a DB round-trip test** (now the tables exist) to `tests/models/test_ml_raw_prediction.py`:

```python
from sqlalchemy import select
from app.models.image import Images
from app.models.user import Users


async def test_raw_prediction_db_roundtrip(db_session):
    # NB: Users.salt is required (no default); match the existing test helpers.
    user = Users(username="rawpred", email="r@e.co", password="x",
                 salt="testsalt12345678", password_type="bcrypt")
    db_session.add(user)
    await db_session.flush()
    img = Images(user_id=user.user_id, filename="f", ext="jpg", md5_hash="m",
                 filesize=1, width=1, height=1, status=1, rating=0)
    db_session.add(img)
    await db_session.flush()

    model = MlModels(name="caformer_b36.dbv4-full")
    tag = MlExternalTags(name="long_hair", category=0)
    db_session.add_all([model, tag])
    await db_session.flush()
    db_session.add(MlRawPredictions(image_id=img.image_id, model_id=model.id,
                                    external_tag_id=tag.id, confidence=0.97))
    await db_session.flush()

    row = (await db_session.execute(
        select(MlRawPredictions).where(MlRawPredictions.image_id == img.image_id)
    )).scalar_one()
    assert row.model_id == model.id and row.external_tag_id == tag.id
```

> If this fails on a missing `Users`/`Images` column, check `app/models/user.py` / `app/models/image.py` and match the existing `_make_user`/`_make_image` helpers in `tests/services/test_ml_suggestion_pipeline.py`.

Run: `uv run pytest tests/models/test_ml_raw_prediction.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/*_add_ml_raw_prediction_tables.py tests/models/test_ml_raw_prediction.py
git commit -m "feat(ml): migration for raw-prediction store tables"
```

---

## Chunk 2: Inference capture (caformer + raw predictions)

### Task 3: Route `caformer*` model names to the animetimm loader

> **Note:** The standalone `scripts/ml_gpu_infer.py` already loads caformer via its generic `load_model` (reads `model.onnx` + `selected_tags.csv` + `preprocess.json` — no dispatch). This task is a **nice-to-have for app-side use** (e.g. the live `MLTagSuggestionService` path), not required for the GPU populate.

**Files:**
- Modify: `app/services/ml_service.py:70-79`
- Test: `tests/services/test_ml_service.py`

- [ ] **Step 1: Write the failing test** — a `caformer*` name must NOT raise `ValueError` at dispatch. Mirror the existing dispatch tests in this file (check how they assert the swinv2 branch; if they call `load_models` with mocked file checks, follow that). Minimal version:

```python
async def test_caformer_name_routes_to_animetimm(monkeypatch):
    from app.config import settings
    from app.services.ml_service import MLTagSuggestionService
    monkeypatch.setattr(settings, "ML_MODEL_NAME", "caformer_b36.dbv4-full")
    svc = MLTagSuggestionService()
    # Should attempt the animetimm loader (FileNotFoundError if model absent),
    # NOT raise ValueError("Unknown ML_MODEL_NAME").
    with pytest.raises((FileNotFoundError,)):
        await svc.load_models()
```

> Check `tests/services/test_ml_service.py` for the existing dispatch-test style and match it (it may monkeypatch the model dir). The key assertion: caformer does not hit the `Unknown ML_MODEL_NAME` branch.

- [ ] **Step 2: Run — fails** (currently raises `ValueError`).

- [ ] **Step 3: Add caformer to the dispatch** (`app/services/ml_service.py`):

```python
        elif (
            model_name.startswith("swinv2_")
            or model_name.startswith("convnext")
            or model_name.startswith("caformer")
        ):
            await self._load_animetimm(model_dir, model_name)
        else:
            raise ValueError(
                f"Unknown ML_MODEL_NAME: {model_name!r}. "
                "Supported values: 'wd-swinv2-tagger-v3' or an animetimm model name "
                "starting with 'swinv2_', 'convnext', or 'caformer'."
            )
```

- [ ] **Step 4: Run — passes.** **Step 5: Commit** (`feat(ml): route caformer* model names to the animetimm loader`).

### Task 4: `generate_raw_predictions` service method (raw, multi-category)

> **Note:** This method is for the **app-coupled path only** (i.e. `MLTagSuggestionService` / live inference via the FastAPI app). It is **not** used by the standalone GPU populate — `scripts/ml_gpu_infer.py` captures raw predictions independently in its own app-free venv. Keep this task (it provides a useful future live/CPU raw-capture path) but it is not on the GPU populate critical path.

**Files:**
- Modify: `app/services/ml_service.py` (add method)
- Test: `tests/services/test_ml_service.py`

- [ ] **Step 1: Write the failing test** — with a fake model returning `{tag, confidence, category}`, `generate_raw_predictions` returns dicts with `external_tag/confidence/category/model_version` and passes `include_categories` through.

```python
async def test_generate_raw_predictions_shape(monkeypatch):
    from app.services.ml_service import MLTagSuggestionService
    from app.services import animetimm_model
    svc = MLTagSuggestionService()
    svc._model_name = "caformer_b36.dbv4-full"

    class FakeModel:
        async def predict(self, path, *, min_confidence, include_categories):
            assert include_categories == {animetimm_model.GENERAL_CATEGORY,
                                          animetimm_model.CHARACTER_CATEGORY}
            return [{"tag": "long_hair", "confidence": 0.9, "category": 0},
                    {"tag": "hatsune_miku", "confidence": 0.8, "category": 4}]
    svc.model = FakeModel()

    out = await svc.generate_raw_predictions(
        "x.jpg",
        include_categories={animetimm_model.GENERAL_CATEGORY, animetimm_model.CHARACTER_CATEGORY},
        min_confidence=0.35,
    )
    assert out == [
        {"external_tag": "long_hair", "confidence": 0.9, "category": 0,
         "model_version": "caformer_b36.dbv4-full"},
        {"external_tag": "hatsune_miku", "confidence": 0.8, "category": 4,
         "model_version": "caformer_b36.dbv4-full"},
    ]
```

- [ ] **Step 2: Run — fails** (no method). **Step 3: Implement** in `MLTagSuggestionService`:

```python
    async def generate_raw_predictions(
        self,
        image_path: str,
        *,
        include_categories: set[int],
        min_confidence: float,
    ) -> list[dict[str, Any]]:
        """Raw predictions across the given categories, for the raw-prediction store.

        Unlike generate_suggestions (general-only, mapped-shape), this returns the
        model's external tags with their category, for any categories requested.
        """
        if not self.model:
            raise RuntimeError("Models not loaded. Call load_models() first.")
        preds = await self.model.predict(
            image_path, min_confidence=min_confidence, include_categories=include_categories
        )
        return [
            {
                "external_tag": p["tag"],
                "confidence": p["confidence"],
                "category": p["category"],
                "model_version": self._model_name,
            }
            for p in preds
        ]
```

- [ ] **Step 4: Run — passes.** **Step 5: Commit** (`feat(ml): generate_raw_predictions (multi-category raw output)`).

### Task 5: `ml_gpu_infer.py` — `--include-character` flag + capture general+character

> **Scope:** `scripts/ml_gpu_infer.py` is the **standalone GPU runner** used on the GPU box ("skinny"). It is app-free (only onnxruntime + numpy + pillow) and runs in a Python 3.12 + onnxruntime-rocm venv. `scripts/ml_backfill_infer.py` is the app-coupled CPU twin (`from app.config import settings`, `from app.services...`) and **cannot run on skinny's app-free venv** — it is not used for the populate. The live theme-only suggestion path is unaffected by this task; `ml_gpu_infer.py` is only used for backfill/populate.

**Files:**
- Modify: `scripts/ml_gpu_infer.py`
- Test: `tests/integration/test_ml_gpu_infer.py`

- [ ] **Step 1: Add `--include-character`** flag (default **on**) to the argparser, controlling the category set passed to `predict()`.

- [ ] **Step 2: Extend `predict()`** — currently it filters to general-only (`if category != GENERAL_CATEGORY: continue`). Change it to also include `CHARACTER_CATEGORY` when `--include-character` is set, and add `category` to each emitted prediction record. The written record stays `{"image_id": ..., "predictions": [...]}` — now each prediction carries `category` (extra key; harmless to the existing `ingest_results`).

- [ ] **Step 3: Test** — update the drift-guard test in `tests/integration/test_ml_gpu_infer.py`: assert that with `--include-character` (default), predictions for both category 0 (general) and category 4 (character) are emitted; assert that without `--include-character`, only general predictions appear. Also confirm `uv run python scripts/ml_gpu_infer.py --help` shows the new flag.

- [ ] **Step 4: Commit** (`feat(ml): gpu infer --include-character flag + capture general+character raw preds`).

---

## Chunk 3: Raw ingest

### Task 6: Populate `ml_external_tags` from `selected_tags.csv`

**Files:**
- Create: `app/services/ml_raw_store.py` (new home for raw-store services)
- Test: `tests/services/test_ml_raw_store.py`

- [ ] **Step 1: Failing test** — `populate_external_tags(db, csv_path)` upserts (name, category) rows idempotently.

```python
async def test_populate_external_tags_idempotent(db_session, tmp_path):
    from app.services.ml_raw_store import populate_external_tags
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("tag_id,name,category\n1,long_hair,0\n2,hatsune_miku,4\n")
    n1 = await populate_external_tags(db_session, csv)
    n2 = await populate_external_tags(db_session, csv)  # second run: no new rows
    from sqlalchemy import select
    from app.models.ml_raw_prediction import MlExternalTags
    rows = (await db_session.execute(select(MlExternalTags))).scalars().all()
    assert {(r.name, r.category) for r in rows} == {("long_hair", 0), ("hatsune_miku", 4)}
    assert n1 == 2 and n2 == 0
```

- [ ] **Step 2: Run — fails. Step 3: Implement** `populate_external_tags` (read CSV `name`,`category`; insert names not already present; return count inserted). Use a select of existing names + `db.add_all` for missing; `await db.commit()`. **Important:** the real animetimm `selected_tags.csv` has the header `name,category,best_threshold` — there is **no `tag_id` column**. Read columns by name (`name` and `category`), not by position, and do not depend on a `tag_id` column. (The synthetic test CSV above uses `tag_id,name,category` for convenience — that is fine for the test, but the implementation must not require `tag_id`.)

- [ ] **Step 4: Run — passes. Step 5: Commit** (`feat(ml): populate ml_external_tags dictionary from selected_tags.csv`).

### Task 7: `ingest_raw_predictions` + `scripts/ml_raw_ingest.py`

**Files:**
- Modify: `app/services/ml_raw_store.py` (add `ingest_raw_predictions`)
- Create: `scripts/ml_raw_ingest.py`
- Test: `tests/services/test_ml_raw_store.py`

- [ ] **Step 1: Failing test** — given an image, a populated `ml_external_tags`, and JSONL-style records `{image_id, predictions:[{external_tag, confidence, model_version, category}]}`, `ingest_raw_predictions` upserts `ml_models` (by model_version), maps `external_tag`→id, and bulk-inserts `ml_raw_predictions`; re-running is idempotent (PK).

```python
async def test_ingest_raw_predictions(db_session, tmp_path):
    # seed image + external-tag dict
    ... # make user+image (see Task 1 helper); populate_external_tags with long_hair/hatsune_miku
    from app.services.ml_raw_store import ingest_raw_predictions
    records = [{"image_id": img.image_id, "predictions": [
        {"external_tag": "long_hair", "confidence": 0.9, "model_version": "caformer_b36.dbv4-full", "category": 0},
        {"external_tag": "hatsune_miku", "confidence": 0.8, "model_version": "caformer_b36.dbv4-full", "category": 4},
    ]}]
    created = await ingest_raw_predictions(db_session, records)
    assert created == 2
    again = await ingest_raw_predictions(db_session, records)  # idempotent
    assert again == 0
    # unknown external_tag is skipped (or logged) — add a record with an unmapped name and assert it doesn't error
```

- [ ] **Step 2: Run — fails. Step 3: Implement** `ingest_raw_predictions(db, records)`:
  - Build/cache `name → external_tag_id` from `ml_external_tags` (one query) and `model_version → model_id` (upsert `ml_models` for unseen versions).
  - For each record's predictions, resolve ids (skip + log external tags not in the dict), collect rows.
  - **Bulk insert** in batches (e.g. 5,000 rows) via SQLAlchemy core with `INSERT IGNORE`:
    `from sqlalchemy.dialects.mysql import insert as mysql_insert` → `stmt = mysql_insert(MlRawPredictions).values(batch).prefix_with("IGNORE")` → `res = await db.execute(stmt)`. Sum `res.rowcount` across batches and return it. **Use `prefix_with("IGNORE")`, NOT `on_duplicate_key_update`** — IGNORE skips existing composite-PK rows and `rowcount` then counts only rows actually inserted, so the test's `created==2` then re-run `==0` idempotency assertion holds (ON DUPLICATE KEY UPDATE inflates MySQL affected-rows and would break it).
  - Commit.

- [ ] **Step 4: CLI `scripts/ml_raw_ingest.py`** — mirror `scripts/ml_backfill_ingest.py` exactly (argparse `results` JSONL files + `--model <name>` to locate `selected_tags.csv`; `async with get_async_session() as db:`). Sequence: `populate_external_tags(db, <ml_models_path>/<model>/selected_tags.csv)` then stream `iter_results` (reuse from `app.services.ml_backfill`) into `ingest_raw_predictions` in batches. Print counts.

- [ ] **Step 5: Run test — passes. Step 6: Commit** (`feat(ml): ingest raw predictions into the store (bulk, idempotent) + CLI`).

---

## Chunk 4: Re-map

### Task 8: Extract `compute_implied_suggestions` shared helper; refactor `store_predictions`

**Files:**
- Modify: `app/services/ml_suggestion_pipeline.py`
- Test: `tests/services/test_ml_suggestion_pipeline.py` (existing tests must still pass + a focused helper test)

- [ ] **Step 1: Write the helper test** — seed mappings/tags so `compute_implied_suggestions(db, image_id, predictions)` returns the resolved internal suggestions, excluding tags already applied to the image and below-threshold ones. Also assert it returns the applied-tag set (used by `store_predictions`).

- [ ] **Step 2: Implement** the helper (lift steps 1–4 + the applied/threshold filters out of `store_predictions`):

```python
async def compute_implied_suggestions(
    db: AsyncSession, image_id: int, predictions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], set[int]]:
    """Resolve external predictions to the internal suggestions the current
    mappings imply for this image: map → resolve aliases/hierarchy → drop
    redundant vs. existing tags → drop already-applied and below-threshold.
    Returns (implied_suggestions, applied_tag_ids). Image-state-dependent.
    """
    mapped = await resolve_external_tags(db, predictions)
    resolved = await resolve_tag_relationships(db, mapped)
    applied = {
        row[0] for row in (await db.execute(
            select(TagLinks.tag_id).where(TagLinks.image_id == image_id)
        )).all()
    }
    filtered = await filter_redundant_suggestions(db, resolved, applied)
    implied = [
        p for p in filtered
        if p["tag_id"] not in applied and p["confidence"] >= settings.ML_MIN_CONFIDENCE
    ]
    return implied, applied
```

- [ ] **Step 3: Refactor `store_predictions`** to call the helper, keeping its own behavior (the approved→pending reset for removed tags + skip-already-suggested):

```python
async def store_predictions(db, image_id, predictions):
    implied, applied = await compute_implied_suggestions(db, image_id, predictions)
    existing = list((await db.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)
    )).scalars().all())
    existing_tag_ids = {s.tag_id for s in existing}
    # reset approved→pending when the tag was removed from the image (unchanged behavior)
    for s in existing:
        if s.status == "approved" and s.tag_id not in applied:
            s.status, s.reviewed_at, s.reviewed_by_user_id = "pending", None, None
    created = 0
    for p in implied:
        if p["tag_id"] in existing_tag_ids:
            continue
        db.add(MlTagSuggestions(image_id=image_id, tag_id=p["tag_id"],
            confidence=p["confidence"], model_version=p["model_version"], status="pending"))
        created += 1
    await db.commit()
    return created
```

- [ ] **Step 4: Run the FULL existing pipeline test file** — behavior must be unchanged.

Run: `uv run pytest tests/services/test_ml_suggestion_pipeline.py -v`
Expected: all PASS (refactor is behavior-preserving).

- [ ] **Step 5: Commit** (`refactor(ml): extract compute_implied_suggestions shared helper`).

### Task 9: `ml_remap` service + `scripts/ml_remap.py`

**Files:**
- Create: `app/services/ml_remap.py`
- Create: `scripts/ml_remap.py`
- Test: `tests/services/test_ml_remap.py`

- [ ] **Step 1: Failing tests** for the reconcile (`remap_image(db, image_id, predictions, model_name)`), each seeding tags + mappings:
  1. **adds pending** for a newly-mapped tag with no existing row;
  2. **deletes a stale pending** (same `model_version`) whose tag is no longer implied; assert it deletes only the SAME-model pending row;
  3. **preserves a pending row with a DIFFERENT `model_version`** — a pending row from another model (e.g. swinv2) must not be deleted when re-mapping with caformer (cross-source clobber guard);
  4. **preserves `approved`/`rejected`** rows and does **not** re-add/re-suggest them (dismissed stays dismissed);
  5. **does NOT reset** an `approved` row whose image-tag was removed (the deliberate divergence from `store_predictions`).

```python
async def test_remap_preserves_rejected(db_session):
    # image with a rejected ml_tag_suggestion for tag T; predictions imply T again
    ...
    from app.services.ml_remap import remap_image
    await remap_image(db_session, image_id, predictions)  # implies T
    rows = (await db_session.execute(select(MlTagSuggestions)
            .where(MlTagSuggestions.image_id == image_id))).scalars().all()
    t = [r for r in rows if r.tag_id == T][0]
    assert t.status == "rejected"          # not resurrected
    assert sum(1 for r in rows if r.tag_id == T) == 1  # no duplicate pending
```

- [ ] **Step 2: Run — fails. Step 3: Implement** `app/services/ml_remap.py`:

```python
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_raw_prediction import MlExternalTags, MlModels, MlRawPredictions
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_suggestion_pipeline import compute_implied_suggestions


async def remap_image(
    db: AsyncSession, image_id: int, predictions: list[dict[str, Any]], model_name: str
) -> int:
    """Re-map raw predictions into ml_tag_suggestions: regenerate the pending set
    from current mappings, preserve approved/rejected, never re-suggest a
    dismissed tag. Returns pending rows added.

    The delete step is scoped to `model_name`: because ml_tag_suggestions has a
    UNIQUE(image_id, tag_id) constraint, re-map scopes its reconcile to its own
    model_version so it never deletes pending rows produced by a different model
    (e.g. swinv2 live path rows are safe during a caformer re-map).
    """
    implied, _applied = await compute_implied_suggestions(db, image_id, predictions)
    implied_by_tag = {p["tag_id"]: p for p in implied}
    existing = list((await db.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image_id)
    )).scalars().all())
    existing_tag_ids = {s.tag_id for s in existing}
    # delete stale pending for THIS model only (tag no longer implied)
    for s in existing:
        if s.status == "pending" and s.model_version == model_name and s.tag_id not in implied_by_tag:
            await db.delete(s)
    # add pending for implied tags with no existing row (any status)
    added = 0
    for tag_id, p in implied_by_tag.items():
        if tag_id in existing_tag_ids:
            continue  # preserve reviewed; never re-suggest a dismissed tag
        db.add(MlTagSuggestions(image_id=image_id, tag_id=tag_id,
            confidence=p["confidence"], model_version=p["model_version"], status="pending"))
        added += 1
    await db.commit()
    return added


async def remap_image_from_store(db: AsyncSession, image_id: int, model_name: str) -> int:
    """Read this image's raw preds for `model_name` from the store, then remap."""
    rows = (await db.execute(
        select(MlExternalTags.name, MlRawPredictions.confidence)
        .join(MlRawPredictions, MlRawPredictions.external_tag_id == MlExternalTags.id)
        .join(MlModels, MlModels.id == MlRawPredictions.model_id)
        .where(MlRawPredictions.image_id == image_id, MlModels.name == model_name)
    )).all()
    predictions = [
        {"external_tag": name, "confidence": conf, "model_version": model_name}
        for name, conf in rows
    ]
    return await remap_image(db, image_id, predictions, model_name)
```

- [ ] **Step 4: CLI `scripts/ml_remap.py`** — mirror `ml_backfill_ingest.py` (argparse `--model <name>`, optional `--image-id` / `--limit`, `--checkpoint`; `async with get_async_session() as db:`). Default: iterate distinct `image_id`s in `ml_raw_predictions` for the model (resumable via checkpoint) and call `remap_image_from_store` per image. Print added counts.

- [ ] **Step 5: Run tests — pass. Step 6: Commit** (`feat(ml): ml_remap (re-map raw preds → suggestions) + CLI`).

---

## Chunk 5: Operational runbook (not code — the populate + re-map sequence)

This is the order to actually use the feature once Chunks 1–4 are merged. Not TDD; a checklist.

- [ ] **Migrate:** `uv run alembic upgrade head` (creates the three tables).
- [ ] **Infer on skinny (caformer, general+character):**
  `python scripts/ml_gpu_infer.py --manifest <manifest.jsonl> --model caformer_b36.dbv4-full --include-character --out caformer_results.jsonl` (shard as needed; resumable). Use the standalone `ml_gpu_infer.py` — it runs in the app-free onnxruntime-rocm venv on skinny. `ml_backfill_infer.py` is app-coupled and cannot run there.
- [ ] **Ingest raw preds (next to the DB):**
  `uv run python scripts/ml_raw_ingest.py caformer_results.jsonl --model caformer_b36.dbv4-full`
  (populates `ml_external_tags` from the model's `selected_tags.csv`, seeds `ml_models`, bulk-loads `ml_raw_predictions`).
- [ ] **Add character mappings:** insert Danbooru-character → internal-character-tag rows into `tag_mappings` (the internal tag's `type` must be the character type so the frontend renders them as character — the panel already keys off `tag.type`).
- [ ] **Re-map:** `uv run python scripts/ml_remap.py --model caformer_b36.dbv4-full` → character (and any newly-mapped theme) suggestions appear as `pending`.
- [ ] **Verify:** spot-check an image via `GET /api/v1/images/{id}/ml-tag-suggestions?status=pending`; iterate on `tag_mappings` + re-map (cheap, no re-inference).

---

## Deferred (from the design — do NOT build here)

Live-path raw-pred persistence on upload/generate; the frontend per-image "Re-run suggestions" button; lower-floor storage for threshold tuning; rating category; swinv2 A/B pass.

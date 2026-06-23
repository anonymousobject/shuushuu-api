# ML Tag Suggestions on Upload — API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. Follow TDD: write the failing test, run it red, implement minimally, run it green, commit.

**Goal:** Add a stateless `POST /api/v1/ml-tag-suggestions/analyze` endpoint that runs ONNX inference on an uploaded image's bytes and returns mapped internal **theme + character** tags, so the frontend can offer them in the upload form before submit — inferring each image **at most once** by caching predictions by MD5 and reusing them in the post-upload worker.

**Architecture:** Inline inference in the single uvicorn process, guarded by a global `asyncio.Semaphore` (concurrency cap) with bounded wait, plus a per-user Redis rate limit. The shared model singleton and the semaphore move into a new `app/services/ml_runtime.py`. The live path routes through `generate_raw_predictions` (general + character, with category) so one inference feeds: the analyze response, the Redis cache, the pending suggestions, and the raw-prediction store. The arq worker reuses the cached predictions when present.

**Tech Stack:** FastAPI · SQLAlchemy async · arq · redis.asyncio · onnxruntime · pytest (httpx AsyncClient + ASGITransport).

**Companion spec:** `../../../shuushuu-frontend/docs/plans/2026-06-23-ml-suggestions-on-upload-design.md` (cross-repo design). This plan covers the **API** only; the frontend is a separate plan.

**Repo hygiene (CRITICAL):** the `shuushuu-api` working tree carries unrelated uncommitted user WIP. NEVER `git add -A`/`.`/`-u` or `git commit -a`. Stage only the exact files named in each task's commit step and verify `git status` first. Commit footer on every commit:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BiQ7vc9MkxJFbL8Hdh8FF8
```

**Run tests:** `uv run pytest <path> -v` (serial). Full suite: `./run-tests.sh`. Do NOT use `make test` (that's docker-compose).

---

## File Structure

**New files:**
- `app/services/ml_categories.py` — **light** module (no onnxruntime) holding category constants + `SUGGESTION_CATEGORIES`. Exists so the always-imported router chain can reference the category set without pulling onnxruntime (the app must start when onnxruntime is absent / the flag is off — current prod state).
- `app/services/ml_runtime.py` — shared model singleton + global inference semaphore (`inference_slot()` async context manager) + `InferenceBusy` (429) + `warm_load_if_enabled()`.
- `app/api/v1/ml_analyze.py` — the `POST /ml-tag-suggestions/analyze` router.
- `app/schemas/ml_analyze.py` — `AnalyzedTag` + `AnalyzeTagsResponse` response models.
- `tests/services/test_ml_runtime.py`, `tests/api/v1/test_ml_analyze.py` — tests for the above.

**Import-weight rule (CRITICAL):** the API currently starts with onnxruntime *absent* (it's lazy-loaded). Nothing imported at app startup (routers and what they import) may import onnxruntime at module level. So `ml_analyze.py` and `ml_suggestion_pipeline.py` must import `SUGGESTION_CATEGORIES` from `ml_categories` (light) — NOT from `ml_service` (which imports onnxruntime). The model singleton is reached only via `ml_runtime.get_ml_service()` (function-local onnxruntime import). Verify after each chunk: `uv run python -c "import app.main; print('ok')"` must succeed even in an env without onnxruntime.

**Modified files:**
- `app/config.py` — new `ML_*` settings (rate limit, concurrency, timeout, display floor, cap, max dimension, intra-op threads, cache TTL).
- `app/services/onnx_providers.py` — `make_session_options(intra_op_threads)` helper.
- `app/services/onnx_model.py` + `app/services/animetimm_model.py` — use the helper in `_load_sync` to cap intra-op threads.
- `app/services/ml_service.py` — `generate_suggestions`/`generate_raw_predictions` include CHARACTER; export `SUGGESTION_CATEGORIES`.
- `app/services/ml_suggestion_pipeline.py` — route `generate_and_store_suggestions` through `generate_raw_predictions` + add `persist_predictions` (ingest raw store + store suggestions).
- `app/api/v1/ml_tag_suggestions.py` — delete the local `_get_ml_service`; import from `ml_runtime`.
- `app/main.py` — warm-load the model from `lifespan` when the flag is on (avoids a ~1.5 s cold load on the first `/analyze`).
- `app/services/rate_limit.py` — `check_analyze_rate_limit`.
- `app/tasks/ml_tag_suggestion_job.py` — reuse cached predictions by MD5; own redis client.
- `app/api/v1/meta.py` — add `ml_tag_suggestions_enabled` to `PublicConfig`.
- wherever routers are registered (likely `app/api/v1/__init__.py` or `app/main.py`) — include `ml_analyze.router`.

---

## Chunk 1: Service & runtime layer

### Task 1: New ML config settings

**Files:**
- Modify: `app/config.py` (after the existing ML block ending at line ~144, before `# Avatar Settings`)
- Test: `tests/test_config.py` (if it exists; else assert via a tiny inline test in `tests/test_ml_runtime.py` Task 3 — search first: `ls tests/test_config.py`)

- [ ] **Step 1: Add settings.** Insert into the `Settings` class in `app/config.py` after `ML_MIN_CONFIDENCE`:

```python
    # ML suggestions on upload (analyze endpoint)
    ML_ANALYZE_RATE_LIMIT: int = Field(
        default=20, description="Max /analyze calls per user per minute"
    )
    ML_ANALYZE_CONCURRENCY: int = Field(
        default=2, description="Max concurrent inferences process-wide (global semaphore)"
    )
    ML_ANALYZE_SEMAPHORE_TIMEOUT: float = Field(
        default=8.0,
        description="Seconds to wait for an inference slot before returning 429",
    )
    ML_ANALYZE_MIN_CONFIDENCE: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Display floor for upload-form suggestions (separate from ML_MIN_CONFIDENCE used for stored suggestions)",
    )
    ML_ANALYZE_MAX_SUGGESTIONS: int = Field(
        default=12, description="Max suggestions returned per tag type from /analyze"
    )
    ML_ANALYZE_MAX_DIMENSION: int = Field(
        default=12000,
        description="Reject images whose longest edge exceeds this many px before inference",
    )
    ML_INTRA_OP_THREADS: int = Field(
        default=0,
        description="onnxruntime intra-op thread cap per inference; 0 = library default (all cores)",
    )
    ML_ANALYZE_CACHE_TTL_SECONDS: int = Field(
        default=3600, description="TTL for the md5 -> raw-predictions analyze cache"
    )
```

- [ ] **Step 2: Verify it imports.** Run: `uv run python -c "from app.config import settings; print(settings.ML_ANALYZE_CONCURRENCY, settings.ML_INTRA_OP_THREADS)"` — Expected: `2 0`.

- [ ] **Step 3: Commit.**
```bash
git add app/config.py
git commit -m "feat(ml): config for upload-analyze (concurrency, rate limit, thread cap, cache TTL)"
```

---

### Task 2: `make_session_options` helper + thread-capped sessions

**Files:**
- Modify: `app/services/onnx_providers.py`
- Modify: `app/services/onnx_model.py` (`_load_sync`, ~lines 56-57)
- Modify: `app/services/animetimm_model.py` (`_load_sync`, ~lines 63-64)
- Test: `tests/services/test_onnx_providers.py` (create if absent)

- [ ] **Step 1: Write the failing test.** Create/append `tests/services/test_onnx_providers.py`:

```python
import onnxruntime as ort

from app.services.onnx_providers import make_session_options


def test_make_session_options_caps_intra_op_threads():
    so = make_session_options(3)
    assert isinstance(so, ort.SessionOptions)
    assert so.intra_op_num_threads == 3
    assert so.inter_op_num_threads == 1


def test_make_session_options_zero_uses_library_default():
    so = make_session_options(0)
    # 0 means "let onnxruntime decide" — we must not force a cap.
    assert so.intra_op_num_threads == 0
```

- [ ] **Step 2: Run it red.** `uv run pytest tests/services/test_onnx_providers.py -v` — Expected: FAIL (`ImportError: cannot import name 'make_session_options'`).

- [ ] **Step 3: Implement.** Add to `app/services/onnx_providers.py`:

```python
import onnxruntime as ort  # type: ignore[import-untyped]


def make_session_options(intra_op_threads: int) -> ort.SessionOptions:
    """Build SessionOptions, optionally capping intra-op threads.

    ``intra_op_threads <= 0`` leaves onnxruntime's default (all cores). A positive
    value caps cores per inference (with inter_op pinned to 1), so that
    ``semaphore_size x intra_op_threads`` is the process-wide CPU ceiling for
    inference and serving keeps headroom.
    """
    so = ort.SessionOptions()
    if intra_op_threads > 0:
        so.intra_op_num_threads = intra_op_threads
        so.inter_op_num_threads = 1
    return so
```

- [ ] **Step 4: Run it green.** `uv run pytest tests/services/test_onnx_providers.py -v` — Expected: PASS.

- [ ] **Step 5: Wire into both model wrappers.** In `app/services/onnx_model.py` `_load_sync`, replace the session construction:

```python
# before:
providers = select_providers(ort.get_available_providers())
self.session = ort.InferenceSession(str(self.model_path), providers=providers)
# after:
from app.config import settings
from app.services.onnx_providers import make_session_options
providers = select_providers(ort.get_available_providers())
self.session = ort.InferenceSession(
    str(self.model_path),
    sess_options=make_session_options(settings.ML_INTRA_OP_THREADS),
    providers=providers,
)
```

Apply the identical change in `app/services/animetimm_model.py` `_load_sync` (~lines 63-64). Prefer module-level imports of `settings` and `make_session_options` if the file already imports from `app.config`; otherwise add them at the top.

- [ ] **Step 6: Verify no import cycle / smoke.** `uv run python -c "import app.services.onnx_model, app.services.animetimm_model; print('ok')"` — Expected: `ok`.

- [ ] **Step 7: Commit.**
```bash
git add app/services/onnx_providers.py app/services/onnx_model.py app/services/animetimm_model.py tests/services/test_onnx_providers.py
git commit -m "feat(ml): cap onnxruntime intra-op threads via ML_INTRA_OP_THREADS"
```

---

### Task 3: `ml_runtime` — shared singleton + inference semaphore

**Files:**
- Create: `app/services/ml_runtime.py`
- Modify: `app/api/v1/ml_tag_suggestions.py` (delete local `_get_ml_service`, lines 378-399; import from runtime)
- Test: `tests/services/test_ml_runtime.py`

- [ ] **Step 1: Write the failing test.** Create `tests/services/test_ml_runtime.py`:

```python
import asyncio

import pytest

from app.services import ml_runtime
from app.services.ml_runtime import InferenceBusy, inference_slot


@pytest.mark.asyncio
async def test_inference_slot_allows_within_capacity(monkeypatch):
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", asyncio.Semaphore(1))
    async with inference_slot():
        pass  # acquired and released without error


@pytest.mark.asyncio
async def test_inference_slot_times_out_to_429(monkeypatch):
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", sem)
    monkeypatch.setattr(ml_runtime.settings, "ML_ANALYZE_SEMAPHORE_TIMEOUT", 0.05)
    await sem.acquire()  # fill the only slot
    with pytest.raises(InferenceBusy) as exc:
        async with inference_slot():
            pass
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_inference_slot_released_on_success(monkeypatch):
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", sem)
    async with inference_slot():
        pass
    assert sem._value == 1  # released back
```

(If the suite isn't configured for `asyncio_mode=auto`, keep the `@pytest.mark.asyncio` markers; check `pyproject.toml`/`pytest.ini` first and match the existing style — most async tests here use plain `async def` with auto mode, in which case drop the markers.)

- [ ] **Step 2: Run it red.** `uv run pytest tests/services/test_ml_runtime.py -v` — Expected: FAIL (`ModuleNotFoundError: app.services.ml_runtime`).

- [ ] **Step 3: Implement `app/services/ml_runtime.py`:**

```python
"""Shared ML runtime for the single API process.

Owns the one model singleton and a global concurrency cap for inference. Both
the per-image generate endpoint and the upload /analyze endpoint use these, so
there is exactly one loaded model and one process-wide inference ceiling.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import HTTPException, status

from app.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.ml_service import MLTagSuggestionService

logger = get_logger(__name__)

_ml_service: "MLTagSuggestionService | None" = None
_inference_semaphore = asyncio.Semaphore(settings.ML_ANALYZE_CONCURRENCY)


class InferenceBusy(HTTPException):
    """429 raised when no inference slot frees up within the wait timeout."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Server is busy running tag inference; please retry shortly.",
            headers={"Retry-After": "5"},
        )


async def get_ml_service() -> "MLTagSuggestionService":
    """Get or lazily load the process-wide ML service singleton.

    Function-local import keeps onnxruntime out of the API import chain until
    the model is actually needed. The global is only set after a successful
    load, so a failed load is retried next call (not cached half-initialised).
    """
    global _ml_service
    if _ml_service is None:
        from app.services.ml_service import MLTagSuggestionService

        service = MLTagSuggestionService()
        await service.load_models()
        _ml_service = service
        logger.info("ml_service_loaded")
    return _ml_service


@asynccontextmanager
async def inference_slot() -> AsyncIterator[None]:
    """Acquire a global inference slot, waiting up to the configured timeout.

    Bounded wait (not fast-reject): callers queue briefly behind in-flight
    inferences and only get a 429 under sustained overload. Waiting coroutines
    suspend (no thread/core held), so the queue itself is free.
    """
    try:
        await asyncio.wait_for(
            _inference_semaphore.acquire(),
            timeout=settings.ML_ANALYZE_SEMAPHORE_TIMEOUT,
        )
    except (asyncio.TimeoutError, TimeoutError):
        raise InferenceBusy() from None
    try:
        yield
    finally:
        _inference_semaphore.release()


async def warm_load_if_enabled() -> None:
    """Pre-load the model at API startup when the feature is on, so the first
    /analyze doesn't eat the ~1.5 s cold load. Never raises: a load failure is
    logged and inference falls back to lazy loading on first request (the worker
    is the strict one that must fail to start)."""
    if not settings.ML_TAG_SUGGESTIONS_ENABLED:
        return
    try:
        await get_ml_service()
    except Exception:
        logger.warning("ml_warm_load_failed", exc_info=True)
```

- [ ] **Step 4: Run it green.** `uv run pytest tests/services/test_ml_runtime.py -v` — Expected: PASS.

- [ ] **Step 5: Point the existing generate endpoint at the runtime singleton.** In `app/api/v1/ml_tag_suggestions.py`:
  - Delete the local singleton block (lines ~378-399: the `_ml_service` global and `_get_ml_service`).
  - Delete the now-unused `if TYPE_CHECKING: from app.services.ml_service import MLTagSuggestionService` block (lines 34-35) if nothing else uses it.
  - Add import: `from app.services.ml_runtime import get_ml_service`.
  - In `generate_ml_tag_suggestions` (line ~354) change `await _get_ml_service()` → `await get_ml_service()`.

- [ ] **Step 6: Verify nothing else referenced the old name.** Run: `grep -rn "_get_ml_service" app/ tests/` — Expected: no matches.

- [ ] **Step 7: Run the existing suggestion-endpoint tests.** `uv run pytest tests/api/v1/test_ml_tag_suggestions.py -v` — Expected: PASS (sync-generate test still green via the relocated singleton).

- [ ] **Step 8: Write the warm-load test.** In `tests/services/test_ml_runtime.py`, add (patch the seam so no real model loads):

```python
async def test_warm_load_calls_get_ml_service_when_enabled(monkeypatch):
    called = False
    async def fake_get():
        nonlocal called; called = True
        return object()
    monkeypatch.setattr(ml_runtime.settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(ml_runtime, "get_ml_service", fake_get)
    await ml_runtime.warm_load_if_enabled()
    assert called is True

async def test_warm_load_noop_when_disabled(monkeypatch):
    called = False
    async def fake_get():
        nonlocal called; called = True
        return object()
    monkeypatch.setattr(ml_runtime.settings, "ML_TAG_SUGGESTIONS_ENABLED", False)
    monkeypatch.setattr(ml_runtime, "get_ml_service", fake_get)
    await ml_runtime.warm_load_if_enabled()
    assert called is False
```
Run red → it passes once `warm_load_if_enabled` exists (added in Step 3); if you wrote Step 3 fully, run `uv run pytest tests/services/test_ml_runtime.py -v` — Expected: PASS.

- [ ] **Step 9: Wire warm-load into the app lifespan.** In `app/main.py` `lifespan` (the startup section, ~lines 94-141), add after the existing startup wiring:

```python
from app.services.ml_runtime import warm_load_if_enabled  # at top of main.py
...
# inside lifespan startup, before `yield`:
await warm_load_if_enabled()
```
This mirrors the worker's startup warm-load (`worker.py:142-148`) but is non-fatal in the API (so the API still serves non-ML traffic if the model is missing). Smoke: `uv run python -c "import app.main; print('ok')"` — Expected: `ok` (and must still succeed with onnxruntime absent, since `warm_load_if_enabled` no-ops when the flag is off and imports onnxruntime only inside `get_ml_service`).

- [ ] **Step 10: Commit.**
```bash
git add app/services/ml_runtime.py app/api/v1/ml_tag_suggestions.py app/main.py tests/services/test_ml_runtime.py
git commit -m "feat(ml): shared ml_runtime singleton + inference semaphore + startup warm-load"
```

---

### Task 4: Category constants + route the live path through the raw store

**Files:**
- Create: `app/services/ml_categories.py` (light)
- Modify: `app/services/ml_service.py` (remove now-dead `generate_suggestions` + its now-unused category imports)
- Modify: `app/services/ml_suggestion_pipeline.py` (`generate_and_store_suggestions`; add `persist_predictions`)
- Test: `tests/services/test_ml_suggestion_pipeline.py` (extend), `tests/services/test_ml_service.py` (remove `generate_suggestions` tests), plus fake-service updates in job/workflow tests.

**Decision (resolves the earlier contradiction):** `generate_raw_predictions(path, *, include_categories, min_confidence)` already returns `{external_tag, confidence, category, model_version}` and takes its categories from the caller. Routing the live path through it with `SUGGESTION_CATEGORIES = {general, character}` gives ONE inference that feeds the raw store AND the suggestions AND adds character coverage. That makes `generate_suggestions` (general-only, no category, no raw-store) dead — so it is **removed**, not edited. `store_predictions` accepts the raw shape (the extra `category` key is ignored by `resolve_external_tags`). `ingest_raw_predictions` + `store_predictions` share one session; `store_predictions` issues the commit.

- [ ] **Step 1: Create the light category module** `app/services/ml_categories.py` (see the import-weight rule above — this must NOT import onnxruntime):

```python
"""ML tagger category constants (light: no onnxruntime import).

The model wrappers (onnx_model.py, animetimm_model.py) define the same numeric
category ids; this module mirrors them so code in the always-imported router
chain can reference the suggestion category set WITHOUT importing onnxruntime.
Keep in sync with the wrappers' constants (general=0, character=4, rating=9).
"""

GENERAL_CATEGORY = 0
CHARACTER_CATEGORY = 4
RATING_CATEGORY = 9

# Categories surfaced as tag suggestions: general (-> internal theme tags) + character.
SUGGESTION_CATEGORIES: set[int] = {GENERAL_CATEGORY, CHARACTER_CATEGORY}
```

- [ ] **Step 2: Verify it's light.** `uv run python -c "import sys, app.services.ml_categories; assert 'onnxruntime' not in sys.modules; print('ok')"` — Expected: `ok`.

- [ ] **Step 3: Write the failing pipeline test.** In `tests/services/test_ml_suggestion_pipeline.py`, add a test that `generate_and_store_suggestions` populates the raw store AND creates suggestions, using a fake service exposing `generate_raw_predictions`. Reuse the file's existing image/fullsize arrange (its `store_predictions`/`generate_and_store_suggestions` tests create an `Images` row and, for path-checking ones, set `settings.STORAGE_PATH=tmp_path` and write `tmp_path/"fullsize"/f"{filename}.{ext}"`). `PIPELINE = "app.services.ml_suggestion_pipeline"`:

```python
async def test_generate_and_store_populates_raw_store_and_suggestions(db_session, tmp_path, monkeypatch):
    image = await _make_image(db_session)            # follow the file's existing arrange
    _write_fullsize(tmp_path, image, monkeypatch)     # sets STORAGE_PATH + writes fullsize file
    raw = [
        {"external_tag": "long_hair", "confidence": 0.92, "category": 0, "model_version": "v3"},
        {"external_tag": "hatsune_miku", "confidence": 0.88, "category": 4, "model_version": "v3"},
    ]

    class FakeService:
        model_name = "v3"
        async def generate_raw_predictions(self, image_path, *, include_categories, min_confidence):
            assert include_categories == {0, 4}      # SUGGESTION_CATEGORIES
            return list(raw)

    captured = {}
    async def fake_ingest(db, records):
        captured["records"] = records
        return sum(len(r["predictions"]) for r in records)

    mapped = [{"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
              {"tag_id": 99, "confidence": 0.88, "model_version": "v3"}]
    with (
        patch(f"{PIPELINE}.ingest_raw_predictions", fake_ingest),
        patch(f"{PIPELINE}.resolve_external_tags", _resolver_to_tag_ids(mapped)),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough_resolver),
    ):
        created = await generate_and_store_suggestions(db_session, image, FakeService())

    assert created == 2
    assert captured["records"] == [{"image_id": image.image_id, "predictions": raw}]
```

- [ ] **Step 4: Run it red.** `uv run pytest tests/services/test_ml_suggestion_pipeline.py -k raw_store -v` — Expected: FAIL (`ingest_raw_predictions` not called / not imported).

- [ ] **Step 5: Implement the refactor** in `app/services/ml_suggestion_pipeline.py`:
  - Add top-level imports (both light — neither pulls onnxruntime): `from app.services.ml_raw_store import ingest_raw_predictions` and `from app.services.ml_categories import SUGGESTION_CATEGORIES`.
  - Add `persist_predictions`:
    ```python
    async def persist_predictions(
        db: AsyncSession, image_id: int, raw_predictions: list[dict[str, Any]]
    ) -> int:
        """Persist one image's raw predictions: populate the raw-prediction store
        AND create pending suggestions. Shared by the live worker (cache hit or
        fresh inference) and generate_and_store_suggestions.

        ingest_raw_predictions and store_predictions run in the SAME session;
        store_predictions issues the commit (do not add a second commit here).
        Returns the number of suggestions created."""
        await ingest_raw_predictions(db, [{"image_id": image_id, "predictions": raw_predictions}])
        return await store_predictions(db, image_id, raw_predictions)
    ```
  - Replace the inference body of `generate_and_store_suggestions` (the `predictions = await ml_service.generate_suggestions(...)` block, ~lines 175-185):
    ```python
    raw_predictions = await ml_service.generate_raw_predictions(
        str(image_path),
        include_categories=SUGGESTION_CATEGORIES,
        min_confidence=settings.ML_MIN_CONFIDENCE,
    )
    logger.info("ml_suggestion_pipeline_predictions_generated", image_id=image_id, count=len(raw_predictions))
    return await persist_predictions(db, image_id, raw_predictions)
    ```

- [ ] **Step 6: Run it green.** `uv run pytest tests/services/test_ml_suggestion_pipeline.py -v` — Expected: PASS.

- [ ] **Step 7: Remove the dead `generate_suggestions`.** Confirm no remaining app callers: `grep -rn "generate_suggestions" app/` — Expected: only the definition in `ml_service.py`. Remove the `generate_suggestions` method (`ml_service.py` ~118-164) and any imports it alone used (`ANIMETIMM_GENERAL`, `GENERAL_CATEGORY` — confirm `generate_raw_predictions` doesn't use them before deleting). Then in `tests/services/test_ml_service.py` delete the `generate_suggestions` tests (the include-set assertions, ~lines 109-174). Verify `uv run python -c "import app.services.ml_service; print('ok')"`.

- [ ] **Step 8: Update remaining fakes.** `grep -rn "generate_suggestions" tests/` and switch any `FakeMLService.generate_suggestions` to `generate_raw_predictions` (returning the raw shape incl. `category`) in `tests/tasks/test_ml_tag_suggestion_job.py` and `tests/integration/test_ml_tag_suggestion_workflow.py`.

- [ ] **Step 9: Run all affected suites.** `uv run pytest tests/services/test_ml_service.py tests/services/test_ml_suggestion_pipeline.py tests/tasks/test_ml_tag_suggestion_job.py tests/integration/test_ml_tag_suggestion_workflow.py tests/api/v1/test_ml_tag_suggestions.py -v` — Expected: PASS.

- [ ] **Step 10: Verify the app imports without onnxruntime in the chain.** `uv run python -c "import app.main; print('ok')"` — Expected: `ok`.

- [ ] **Step 11: Commit.**
```bash
git add app/services/ml_categories.py app/services/ml_service.py app/services/ml_suggestion_pipeline.py tests/services/test_ml_service.py tests/services/test_ml_suggestion_pipeline.py tests/tasks/test_ml_tag_suggestion_job.py tests/integration/test_ml_tag_suggestion_workflow.py
git commit -m "feat(ml): live path infers general+character via raw store; drop dead generate_suggestions"
```

---

## Chunk 2: API surface (analyze endpoint, rate limit, config flag)

### Task 5: Per-user analyze rate limit

**Files:**
- Modify: `app/services/rate_limit.py` (mirror `check_similarity_rate_limit`)
- Test: `tests/services/test_rate_limit.py` (extend; mirror existing similarity-limit tests)

- [ ] **Step 1: Write the failing test** (mirror the existing similarity rate-limit test — uses a fake/real redis with `get`/`incr`/`expire`/`pipeline`):

```python
async def test_check_analyze_rate_limit_raises_429_over_limit(monkeypatch):
    monkeypatch.setattr(settings, "ML_ANALYZE_RATE_LIMIT", 2)
    redis_client = <real or fake redis as the similarity test uses>
    # 2 calls allowed
    await check_analyze_rate_limit(user_id=1, redis_client=redis_client)
    await check_analyze_rate_limit(user_id=1, redis_client=redis_client)
    with pytest.raises(HTTPException) as exc:
        await check_analyze_rate_limit(user_id=1, redis_client=redis_client)
    assert exc.value.status_code == 429
```

- [ ] **Step 2: Run it red.** `uv run pytest tests/services/test_rate_limit.py -k analyze -v` — Expected: FAIL (function missing).

- [ ] **Step 3: Implement** `check_analyze_rate_limit` in `app/services/rate_limit.py`, copying the `check_similarity_rate_limit` body exactly but with key `f"ml_analyze_rate:{user_id}"` and limit `settings.ML_ANALYZE_RATE_LIMIT` (keep the graceful-degradation try/except: Redis errors must not block analyze).

- [ ] **Step 4: Run it green.** `uv run pytest tests/services/test_rate_limit.py -k analyze -v` — Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add app/services/rate_limit.py tests/services/test_rate_limit.py
git commit -m "feat(ml): per-user redis rate limit for the analyze endpoint"
```

---

### Task 6: Analyze response schema

**Files:**
- Create: `app/schemas/ml_analyze.py`
- Test: covered by Task 7's endpoint tests (schema is exercised there)

- [ ] **Step 1: Implement `app/schemas/ml_analyze.py`:**

```python
"""Response schema for the upload-form analyze endpoint."""

from pydantic import BaseModel


class AnalyzedTag(BaseModel):
    """A single resolved internal tag suggestion (no confidence — not displayed)."""

    tag_id: int
    title: str
    type: int  # internal tag type: theme=1, source=2, artist=3, character=4


class AnalyzeTagsResponse(BaseModel):
    """Theme + character suggestions for an uploaded image, in display order."""

    suggestions: list[AnalyzedTag]
```

- [ ] **Step 2: Verify import.** `uv run python -c "from app.schemas.ml_analyze import AnalyzeTagsResponse; print('ok')"` — Expected: `ok`.

- [ ] **Step 3: Commit.**
```bash
git add app/schemas/ml_analyze.py
git commit -m "feat(ml): analyze response schema (tag_id/title/type, no confidence)"
```

---

### Task 7: The `analyze` endpoint

**Files:**
- Create: `app/api/v1/ml_analyze.py`
- Modify: router registration (find with `grep -rn "include_router" app/`)
- Test: `tests/api/v1/test_ml_analyze.py`

**Behavior:** `POST /api/v1/ml-tag-suggestions/analyze`, multipart `file`. Verified user. 503 when flag off. Per-user rate limit. Read bytes (413 if over `MAX_IMAGE_SIZE`). Compute MD5. Decode with PIL to validate + enforce `ML_ANALYZE_MAX_DIMENSION` (400 on bad/oversize). Acquire `inference_slot()` (429 on timeout). Write bytes to a temp file; `generate_raw_predictions(SUGGESTION_CATEGORIES, min_confidence=ML_ANALYZE_MIN_CONFIDENCE)`; delete temp. Cache raw JSON under `ml:analyze:<md5>` with TTL. Resolve via `resolve_external_tags`; fetch `Tags` for title+type; per-type sort by confidence desc, apply floor, cap at `ML_ANALYZE_MAX_SUGGESTIONS`. Return `AnalyzeTagsResponse`.

- [ ] **Step 1: Write the failing tests.** Create `tests/api/v1/test_ml_analyze.py`. **Copy** the `verified_user` fixture into this file — it's defined locally in `tests/api/v1/test_upload.py` (lines ~18-39), NOT in conftest, so it can't be imported as a shared fixture. Use the same `create_access_token(user.id)` + Bearer pattern, the `client` fixture, and patch the inference seam (`app.api.v1.ml_analyze.get_ml_service`) to return a fake service with `generate_raw_predictions`. Patch `resolve_external_tags` at the endpoint module. Cover:

```python
# 1. flag off -> 503
async def test_analyze_503_when_disabled(client, verified_user, monkeypatch):
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)
    r = await _post_analyze(client, verified_user, _fake_jpeg())
    assert r.status_code == 503

# 2. unauthenticated -> 401
async def test_analyze_requires_auth(client):
    r = await client.post("/api/v1/ml-tag-suggestions/analyze",
                          files={"file": ("a.jpg", _fake_jpeg(), "image/jpeg")})
    assert r.status_code == 401

# 3. happy path -> theme + character tags, sorted, mapped
async def test_analyze_returns_theme_and_character_tags(client, verified_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    raw = [{"external_tag": "long_hair", "confidence": 0.9, "category": 0, "model_version": "v3"},
           {"external_tag": "hatsune_miku", "confidence": 0.8, "category": 4, "model_version": "v3"}]
    # seed Tags rows 46 (theme) + 99 (character); patch get_ml_service + resolve_external_tags
    ...
    r = await _post_analyze(client, verified_user, _fake_jpeg())
    assert r.status_code == 200
    body = r.json()
    titles = {s["title"] for s in body["suggestions"]}
    assert "long hair" in titles and "hatsune miku" in titles

# 4. oversize dimensions -> 400 (build a PIL image bigger than a low monkeypatched ML_ANALYZE_MAX_DIMENSION)
# 5. busy -> 429 (monkeypatch ml_runtime._inference_semaphore to a filled Semaphore(1) + tiny timeout)
# 6. caches raw predictions under ml:analyze:<md5> (assert via app_real_redis/client_real_redis or a redis set spy)
```

- [ ] **Step 2: Run red.** `uv run pytest tests/api/v1/test_ml_analyze.py -v` — Expected: FAIL (router/endpoint missing → 404s).

- [ ] **Step 3: Implement `app/api/v1/ml_analyze.py`:**

```python
"""Stateless upload-form tag analysis: infer + map tags for image bytes."""

import hashlib
import io
import json
import os
import tempfile
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import VerifiedUser
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.tag import Tags
from app.schemas.ml_analyze import AnalyzedTag, AnalyzeTagsResponse
from app.services.ml_categories import SUGGESTION_CATEGORIES  # light: no onnxruntime
from app.services.ml_runtime import get_ml_service, inference_slot
from app.services.rate_limit import check_analyze_rate_limit
from app.services.tag_mapping_service import resolve_external_tags

logger = get_logger(__name__)

router = APIRouter(prefix="/ml-tag-suggestions", tags=["ml-tag-suggestions"])


@router.post("/analyze", response_model=AnalyzeTagsResponse)
async def analyze_image_tags(
    current_user: VerifiedUser,
    file: Annotated[UploadFile, File(description="Image file to analyze")],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: AsyncSession = Depends(get_db),
) -> AnalyzeTagsResponse:
    if not settings.ML_TAG_SUGGESTIONS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ML tag suggestions are disabled",
        )

    await check_analyze_rate_limit(current_user.id, redis_client)

    content = await file.read()
    if len(content) > settings.MAX_IMAGE_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="File too large")
    md5 = hashlib.md5(content).hexdigest()

    # Validate + dimension guard before inference.
    try:
        with Image.open(io.BytesIO(content)) as probe:
            w, h = probe.size
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid image file") from exc
    if max(w, h) > settings.ML_ANALYZE_MAX_DIMENSION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Image dimensions too large to analyze")

    # Inference (bounded-wait slot). Write to a temp file: predict() takes a path.
    suffix = os.path.splitext(file.filename or "")[1] or ".img"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        ml_service = await get_ml_service()
        async with inference_slot():
            raw = await ml_service.generate_raw_predictions(
                tmp_path,
                include_categories=SUGGESTION_CATEGORIES,
                min_confidence=settings.ML_ANALYZE_MIN_CONFIDENCE,
            )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Cache raw predictions by md5 so the post-upload worker reuses them (infer once).
    try:
        await redis_client.set(
            f"ml:analyze:{md5}", json.dumps(raw),
            ex=settings.ML_ANALYZE_CACHE_TTL_SECONDS,
        )
    except Exception:  # cache is best-effort; never fail analyze on Redis error
        logger.warning("ml_analyze_cache_write_failed", md5=md5, exc_info=True)

    return await _resolve_to_response(db, raw)


async def _resolve_to_response(db: AsyncSession, raw: list[dict]) -> AnalyzeTagsResponse:
    """Map raw external preds -> internal tags, attach title/type, sort+floor+cap per type."""
    resolved = await resolve_external_tags(db, raw)  # [{tag_id, confidence, model_version}]
    if not resolved:
        return AnalyzeTagsResponse(suggestions=[])

    # Keep the best confidence per tag_id (resolve can collapse aliases to one id).
    best: dict[int, float] = {}
    for r in resolved:
        tid = r["tag_id"]
        best[tid] = max(best.get(tid, 0.0), r["confidence"])

    tags_result = await db.execute(select(Tags).where(Tags.tag_id.in_(list(best))))  # type: ignore[union-attr]
    tags_by_id = {t.tag_id: t for t in tags_result.scalars().all()}

    # Group by internal type, sort by confidence desc, apply floor + per-type cap.
    # NOTE: this floor is on the MAPPING-SCALED confidence (resolve_external_tags
    # multiplied each by mapping.confidence), so it is NOT redundant with the
    # pre-mapping inference floor — a 0.6 raw prediction can fall below 0.5 here.
    # Tags with a NULL title (Tags.title is nullable) are dropped — unusable as a chip.
    by_type: dict[int, list[tuple[float, Tags]]] = {}
    for tid, conf in best.items():
        tag = tags_by_id.get(tid)
        if tag is None or not tag.title or conf < settings.ML_ANALYZE_MIN_CONFIDENCE:
            continue
        by_type.setdefault(tag.type, []).append((conf, tag))

    suggestions: list[AnalyzedTag] = []
    for _type, rows in by_type.items():
        rows.sort(key=lambda x: x[0], reverse=True)
        for _conf, tag in rows[: settings.ML_ANALYZE_MAX_SUGGESTIONS]:
            suggestions.append(AnalyzedTag(tag_id=tag.tag_id, title=tag.title, type=tag.type))
    return AnalyzeTagsResponse(suggestions=suggestions)
```

(Confirm `Tags` has `.type` and `.title` columns — check `app/models/tag.py`. Confirm `current_user.id` is the right attribute (the similarity limiter uses `current_user.id`).)

- [ ] **Step 4: Register the router.** `grep -rn "include_router" app/` to find the aggregation point (e.g. `app/api/v1/__init__.py` or `app/main.py`), then add `from app.api.v1 import ml_analyze` and `api_router.include_router(ml_analyze.router)` next to the existing `ml_tag_suggestions` include.

- [ ] **Step 5: Run green.** `uv run pytest tests/api/v1/test_ml_analyze.py -v` — Expected: PASS (all cases).

- [ ] **Step 6: Commit.**
```bash
git add app/api/v1/ml_analyze.py tests/api/v1/test_ml_analyze.py <router-registration-file>
git commit -m "feat(ml): POST /ml-tag-suggestions/analyze (verified, rate-limited, semaphore-guarded, md5-cached)"
```

---

### Task 8: Expose the feature flag in PublicConfig

**Files:**
- Modify: `app/api/v1/meta.py`
- Test: `tests/api/v1/test_meta.py` (create/extend)

- [ ] **Step 1: Write the failing test:**

```python
async def test_public_config_exposes_ml_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    r = await client.get("/api/v1/meta/config")
    assert r.status_code == 200
    assert r.json()["ml_tag_suggestions_enabled"] is True
```

- [ ] **Step 2: Run red.** `uv run pytest tests/api/v1/test_meta.py -k ml_flag -v` — Expected: FAIL (`KeyError`).

- [ ] **Step 3: Implement.** Add `ml_tag_suggestions_enabled: bool` to `PublicConfig` and `ml_tag_suggestions_enabled=settings.ML_TAG_SUGGESTIONS_ENABLED` to the `get_public_config` return.

- [ ] **Step 4: Run green.** `uv run pytest tests/api/v1/test_meta.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add app/api/v1/meta.py tests/api/v1/test_meta.py
git commit -m "feat(ml): expose ml_tag_suggestions_enabled in /meta/config"
```

---

## Chunk 3: Worker reuse + deployment

### Task 9: Worker reuses cached predictions by MD5

**Files:**
- Modify: `app/tasks/ml_tag_suggestion_job.py`
- Test: `tests/tasks/test_ml_tag_suggestion_job.py` (extend)

**Behavior:** the job reads the image's stored `md5_hash`, GETs `ml:analyze:<md5>` from Redis (own client on `REDIS_URL`, db 0 — same as `get_redis`). On hit: `persist_predictions(db, image_id, cached_raw)` (no inference). On miss/Redis-error: fall back to `generate_and_store_suggestions` (which infers + persists). Either way the raw store is populated and character is included.

- [ ] **Step 1: Write the failing tests** (mirror the existing job tests: direct call with fake `ctx`, `patch(f"{JOB}.get_async_session", ...)`):

```python
JOB = "app.tasks.ml_tag_suggestion_job"

async def test_job_reuses_cached_predictions_without_inference(db_session, monkeypatch, tmp_path):
    image = await _make_image(db_session, md5_hash="abc123")
    raw = [{"external_tag": "long_hair", "confidence": 0.9, "category": 0, "model_version": "v3"}]
    fake_redis = <client returning json.dumps(raw) for GET ml:analyze:abc123>
    persisted = {}
    async def fake_persist(db, image_id, preds):
        persisted["preds"] = preds; return len(preds)
    # ml_service in ctx must NOT be called
    sentinel = object()
    with (
        patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)),
        patch(f"{JOB}._analyze_redis", return_value=fake_redis),       # see impl
        patch(f"{JOB}.persist_predictions", fake_persist),
    ):
        result = await generate_ml_tag_suggestions({"ml_service": sentinel}, image_id=image.image_id)
    assert result["status"] == "completed"
    assert persisted["preds"] == raw

async def test_job_falls_back_to_inference_on_cache_miss(db_session, monkeypatch, tmp_path):
    # fake_redis GET returns None -> generate_and_store_suggestions is called
    ...
```

- [ ] **Step 2: Run red.** `uv run pytest tests/tasks/test_ml_tag_suggestion_job.py -k cached -v` — Expected: FAIL.

- [ ] **Step 3: Implement.** In `app/tasks/ml_tag_suggestion_job.py`:
  - Add a small helper for the worker's own client (testable seam):
    ```python
    def _analyze_redis() -> "redis.Redis":
        import redis.asyncio as redis
        return redis.from_url(str(settings.REDIS_URL), encoding="utf-8", decode_responses=True)
    ```
    (import `settings` from `app.config`.)
  - After loading `image` and validating `ml_service`, before calling the pipeline:
    ```python
    cached_raw = None
    if image.md5_hash:
        try:
            client = _analyze_redis()
            try:
                blob = await client.get(f"ml:analyze:{image.md5_hash}")
            finally:
                await client.close()
            if blob:
                cached_raw = json.loads(blob)
        except Exception:
            logger.warning("ml_tag_suggestion_job_cache_read_failed", image_id=image_id, exc_info=True)

    if cached_raw is not None:
        suggestions_created = await persist_predictions(db, image_id, cached_raw)
    else:
        suggestions_created = await generate_and_store_suggestions(db, image, ml_service)
    ```
  - Add imports: `import json`, `from app.config import settings`, `from app.services.ml_suggestion_pipeline import persist_predictions` (keep the existing `generate_and_store_suggestions` import).
  - Confirm `Images.md5_hash` is the correct column name (`grep -n "md5" app/models/image.py`).

- [ ] **Step 4: Run green.** `uv run pytest tests/tasks/test_ml_tag_suggestion_job.py -v` — Expected: PASS (cache-hit skips inference; miss falls back).

- [ ] **Step 5: Commit.**
```bash
git add app/tasks/ml_tag_suggestion_job.py tests/tasks/test_ml_tag_suggestion_job.py
git commit -m "feat(ml): worker reuses md5-cached analyze predictions (infer once)"
```

---

### Task 10: Full suite + type/lint gate

- [ ] **Step 1:** `./run-tests.sh` — Expected: all green (no regressions in upload, ml, worker, meta suites).
- [ ] **Step 2:** Run the repo's type checker + linter as configured (check `Makefile`/`.pre-commit-config.yaml`: likely `uv run mypy app` and `uv run ruff check app`). Fix issues. Do NOT auto-format unrelated files.
- [ ] **Step 3: Commit** any fixups (named files only).

---

### Task 11: Deployment enablement (prod prerequisite — checklist, not TDD)

> ML is currently **unwired on prod** (`/srv/shuushuu/shuushuu-api`, host `kyouko`): onnxruntime absent, no model, no compose volume, no `ML_` env, and the raw-store **vocab + tag_mappings are empty** on prod. This chunk makes ML runnable there. It is the prerequisite for the feature on prod and can be executed when ready to ship (the feature is fully buildable/testable on dev/test, which already have model + onnxruntime + vocab + mappings). Treat each item as verify-then-act; nothing here should `git add -A`.

- [ ] **Dependencies:** Inspect `pyproject.toml` dependency groups (`grep -n "onnxruntime\|pillow\|numpy" pyproject.toml`). onnxruntime is currently excluded by the prod `uv sync --frozen --no-dev`. Move the ML runtime deps (onnxruntime + pillow + numpy if dev-only) into the main dependencies (or a dedicated extra installed in the prod Dockerfile). Re-lock with `uv lock`. Rebuild the image so the prod venv contains onnxruntime. Verify: `docker exec shuushuu-api-api-14 /app/.venv/bin/python -c "import onnxruntime; print(onnxruntime.__version__)"`.
- [ ] **Model artifact:** Place `ml_models/swinv2_base_window8_256.dbv4-full/{model.onnx,selected_tags.csv}` (449 MB) at `/srv/shuushuu/shuushuu-api/ml_models/...` (scp from dev). Add a read-only bind mount to BOTH the `api` and `arq-worker` services in `docker-compose.prod.yml`: `- ./ml_models:/app/ml_models:ro`. (Bind-mount, not bake — keeps the image small.)
- [ ] **Env:** Add to `/srv/shuushuu/shuushuu-api/.env.prod`: `ML_TAG_SUGGESTIONS_ENABLED=true`, `ML_MODEL_NAME=swinv2_base_window8_256.dbv4-full`, `ML_MODELS_PATH=ml_models`, `ML_INTRA_OP_THREADS=3` (8-core VM: `semaphore 2 × 3 = 6` cores peak, ~2 free for serving), and any non-default analyze knobs.
- [ ] **Raw-store vocab:** Run `populate_external_tags` on prod with the model's `selected_tags.csv` (else `ingest_raw_predictions` silently drops every prediction as "unknown tag"). Use the existing `scripts/ml_raw_ingest.py`/`populate_external_tags` entrypoint inside the container.
- [ ] **tag_mappings:** Populate prod `tag_mappings` (environment-bound — internal_tag_ids must match prod's `tags`). Generate against prod data or import a prod-validated CSV via `scripts/import_tag_mappings.py`. Without mappings, `resolve_external_tags` returns nothing and analyze yields zero chips. (This is the biggest prod-data step; see the cross-repo design's "Deployment notes" and the `tag-source-link-data-gap` memory for the clean-subset character-mapping caveat.)
- [ ] **Deploy + verify:** recreate `api` + `arq-worker` (rebuild for deps, recreate for compose/env). Verify the worker loaded the model (startup log `ml_service_loaded`/`animetimm_model_loaded`), then verify analyze end-to-end with a real authenticated request returning mapped tags. Confirm the VM still has serving headroom under an inference.
- [ ] **No commit of secrets:** `.env.prod` lives only on the host (not in git). Compose/Dockerfile/pyproject changes are committed (named files only).

---

## Execution notes

- After Chunk 1 and Chunk 2, the analyze endpoint is functional on dev (model + onnxruntime + vocab + mappings already present there) — manually smoke it: `ML_TAG_SUGGESTIONS_ENABLED=true` is already set in dev `.env`; `curl -F file=@<image> -H "Authorization: Bearer <token>" localhost:8000/api/v1/ml-tag-suggestions/analyze`.
- The frontend plan (separate) consumes `/meta/config`'s `ml_tag_suggestions_enabled` and `POST /ml-tag-suggestions/analyze`; regen of the frontend's `api-generated.ts` happens there.

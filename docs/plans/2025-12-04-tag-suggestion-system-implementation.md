# Tag Suggestion System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement ML-powered tag suggestion system that suggests tags for uploaded images using a hybrid approach (custom theme model + Danbooru model) with human review workflow.

**Architecture:** Database tables for storing suggestions → API endpoints for fetching/reviewing → Background Arq job for ML inference → Mock ML models initially (replace with real models in Phase 2).

**Tech Stack:** FastAPI, SQLModel, Alembic (migrations), Arq (background jobs), Pytest, MariaDB

**Design Document:** `docs/plans/2025-12-04-tag-suggestion-system-design.md`

---

## Status: Phase 1 Complete ✓

Phase 1 (mock ML implementation) has been fully implemented and tested. All code follows established codebase patterns.

### Completed Components

| Component | File | Status |
|-----------|------|--------|
| TagSuggestion Model | `app/models/tag_suggestion.py` | ✓ Complete |
| TagMapping Model | `app/models/tag_mapping.py` | ✓ Complete |
| MLModelVersion Model | `app/models/ml_model_version.py` | ✓ Complete |
| Pydantic Schemas | `app/schemas/tag_suggestion.py` | ✓ Complete |
| ML Service (Mock) | `app/services/ml_service.py` | ✓ Complete |
| Tag Resolver | `app/services/tag_resolver.py` | ✓ Complete |
| API Endpoints | `app/api/v1/tag_suggestions.py` | ✓ Complete |
| Background Job | `app/tasks/tag_suggestion_job.py` | ✓ Complete |
| Worker Registration | `app/tasks/worker.py` | ✓ Complete |
| Upload Integration | `app/api/v1/images.py:1453` | ✓ Complete |
| Alembic Migration | `alembic/versions/9fe6ac38cb5c_add_tag_suggestion_tables.py` | ✓ Complete |

### Completed Tests

| Test File | Coverage |
|-----------|----------|
| `tests/models/test_tag_suggestion.py` | Model creation, unique constraints |
| `tests/schemas/test_tag_suggestion_schemas.py` | Schema validation |
| `tests/api/v1/test_tag_suggestions.py` | GET/POST endpoints, auth, permissions |
| `tests/tasks/test_tag_suggestion_job.py` | Background job execution |
| `tests/integration/test_tag_suggestion_workflow.py` | End-to-end workflow |

---

## Phase 2: Real ML Models (Pending)

Replace mock ML service with real model inference.

### Task 2.1: Download and Integrate WD14 Tagger Model

**Files:**
- Modify: `app/services/ml_service.py`
- Create: `app/services/onnx_model.py`

**Step 1: Download WD14 Tagger ONNX model**

```bash
# Download from HuggingFace
mkdir -p ml_models/wd14
wget -O ml_models/wd14/model.onnx https://huggingface.co/SmilingWolf/wd-v1-4-vit-tagger-v2/resolve/main/model.onnx
wget -O ml_models/wd14/selected_tags.csv https://huggingface.co/SmilingWolf/wd-v1-4-vit-tagger-v2/resolve/main/selected_tags.csv
```

**Step 2: Create ONNX inference wrapper**

```python
# app/services/onnx_model.py

"""
ONNX Model Inference Wrapper

Provides GPU-accelerated inference for WD14 and custom models.
"""

import asyncio
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image


class ONNXModel:
    """ONNX model wrapper with GPU support."""

    def __init__(self, model_path: str, labels_path: str | None = None):
        self.model_path = Path(model_path)
        self.labels_path = Path(labels_path) if labels_path else None
        self.session = None
        self.labels: list[str] = []
        self.input_name = ""
        self.input_shape: tuple = ()

    async def load(self) -> None:
        """Load model into memory (run in thread pool to avoid blocking)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self) -> None:
        """Synchronous model loading."""
        # Prefer GPU if available
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)

        # Get input details
        input_info = self.session.get_inputs()[0]
        self.input_name = input_info.name
        self.input_shape = tuple(input_info.shape[1:3])  # (height, width)

        # Load labels if provided
        if self.labels_path and self.labels_path.exists():
            import pandas as pd
            df = pd.read_csv(self.labels_path)
            self.labels = df["name"].tolist()

    async def predict(self, image_path: str) -> list[dict]:
        """
        Run inference on an image.

        Returns list of dicts: {tag: str, confidence: float}
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._predict_sync, image_path)

    def _predict_sync(self, image_path: str) -> list[dict]:
        """Synchronous prediction."""
        if not self.session:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Preprocess image
        img = Image.open(image_path).convert("RGB")
        img = img.resize(self.input_shape)
        img_array = np.array(img).astype(np.float32) / 255.0
        img_array = np.expand_dims(img_array, axis=0)

        # Run inference
        outputs = self.session.run(None, {self.input_name: img_array})
        predictions = outputs[0][0]  # First output, first batch

        # Format results
        results = []
        for idx, confidence in enumerate(predictions):
            if idx < len(self.labels):
                results.append({
                    "tag": self.labels[idx],
                    "confidence": float(confidence),
                })

        return results

    async def cleanup(self) -> None:
        """Release resources."""
        self.session = None
```

**Step 3: Update MLTagSuggestionService**

```python
# app/services/ml_service.py (updated)

"""
ML Tag Suggestion Service

PHASE 2: Real ONNX model inference with WD14 Tagger.
"""

from pathlib import Path

from app.config import settings
from app.services.onnx_model import ONNXModel


class MLTagSuggestionService:
    """
    ML Tag Suggestion Service

    Uses WD14 Tagger (Danbooru) and optional custom theme model.
    """

    def __init__(self):
        self.danbooru_model: ONNXModel | None = None
        self.custom_model: ONNXModel | None = None

    async def load_models(self) -> None:
        """Load ML models into memory."""
        # Load WD14 Tagger (Danbooru)
        danbooru_path = Path(settings.ML_MODELS_PATH) / "wd14" / "model.onnx"
        labels_path = Path(settings.ML_MODELS_PATH) / "wd14" / "selected_tags.csv"

        if danbooru_path.exists():
            self.danbooru_model = ONNXModel(str(danbooru_path), str(labels_path))
            await self.danbooru_model.load()

        # Load custom theme model if available
        custom_path = Path(settings.ML_MODELS_PATH) / "custom_theme" / "model.onnx"
        if custom_path.exists():
            self.custom_model = ONNXModel(str(custom_path))
            await self.custom_model.load()

    async def generate_suggestions(
        self,
        image_path: str,
        min_confidence: float = 0.35  # WD14 uses lower threshold
    ) -> list[dict]:
        """Generate tag suggestions for an image."""
        suggestions = []

        # Run Danbooru model
        if self.danbooru_model:
            danbooru_preds = await self.danbooru_model.predict(image_path)
            for pred in danbooru_preds:
                if pred["confidence"] >= min_confidence:
                    suggestions.append({
                        "external_tag": pred["tag"],
                        "confidence": pred["confidence"],
                        "model_source": "danbooru",
                        "model_version": "wd14_v2",
                    })

        # Run custom model
        if self.custom_model:
            custom_preds = await self.custom_model.predict(image_path)
            for pred in custom_preds:
                if pred["confidence"] >= min_confidence:
                    suggestions.append({
                        "tag_id": pred["tag_id"],  # Custom model outputs tag IDs
                        "confidence": pred["confidence"],
                        "model_source": "custom_theme",
                        "model_version": "v1",
                    })

        return suggestions

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.danbooru_model:
            await self.danbooru_model.cleanup()
        if self.custom_model:
            await self.custom_model.cleanup()
```

**Step 4: Commit**

```bash
git add app/services/onnx_model.py app/services/ml_service.py
git commit -m "feat: integrate WD14 Tagger ONNX model

- Add ONNXModel wrapper with GPU support
- Update MLTagSuggestionService to use real models
- Support both Danbooru (WD14) and custom theme models
- Async-safe inference via thread pool"
```

---

### Task 2.2: Create Tag Mapping Service

**Files:**
- Create: `app/services/tag_mapping_service.py`
- Test: `tests/services/test_tag_mapping_service.py`

**Purpose:** Map Danbooru tags from WD14 model to internal tag IDs.

**Step 1: Write the test**

```python
# tests/services/test_tag_mapping_service.py

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_mapping import TagMapping
from app.models.user import Users
from app.services.tag_mapping_service import resolve_external_tags


@pytest.mark.asyncio
async def test_resolve_danbooru_tags_to_internal(db_session: AsyncSession):
    """Test mapping Danbooru tags to internal tag IDs."""
    user = Users(username="test", email="test@example.com", password="hashed",
                 password_type="bcrypt", salt="testsalt12345678", active=1)
    db_session.add(user)
    await db_session.flush()

    # Create internal tag
    tag = Tags(title="long hair", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    # Create mapping: danbooru "long_hair" → internal "long hair"
    mapping = TagMapping(
        external_tag="long_hair",
        external_source="danbooru",
        internal_tag_id=tag.tag_id,
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    # Resolve suggestions
    suggestions = [
        {"external_tag": "long_hair", "confidence": 0.9, "model_source": "danbooru"}
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 1
    assert resolved[0]["tag_id"] == tag.tag_id
    assert resolved[0]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_ignore_unmapped_tags(db_session: AsyncSession):
    """Test that unmapped tags with NULL internal_tag_id are ignored."""
    # Create mapping with NULL internal_tag_id (means ignore)
    mapping = TagMapping(
        external_tag="1girl",
        external_source="danbooru",
        internal_tag_id=None,  # Ignore this tag
        confidence=1.0,
    )
    db_session.add(mapping)
    await db_session.commit()

    suggestions = [
        {"external_tag": "1girl", "confidence": 0.95, "model_source": "danbooru"}
    ]
    resolved = await resolve_external_tags(db_session, suggestions)

    assert len(resolved) == 0  # Tag was ignored
```

**Step 2: Write the implementation**

```python
# app/services/tag_mapping_service.py

"""
Tag Mapping Service

Resolves external tags (from Danbooru, etc.) to internal tag IDs.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag_mapping import TagMapping


async def resolve_external_tags(
    db: AsyncSession,
    suggestions: list[dict],
) -> list[dict]:
    """
    Resolve external tags to internal tag IDs.

    Args:
        db: Database session
        suggestions: List of dicts with external_tag, confidence, model_source

    Returns:
        List of resolved suggestions with tag_id instead of external_tag.
        Unmapped tags and tags with NULL internal_tag_id are excluded.
    """
    # Collect unique external tags
    external_tags = {s["external_tag"] for s in suggestions if "external_tag" in s}

    if not external_tags:
        # No external tags to resolve, return suggestions with tag_id as-is
        return [s for s in suggestions if "tag_id" in s]

    # Batch fetch mappings
    result = await db.execute(
        select(TagMapping).where(
            TagMapping.external_tag.in_(external_tags),
            TagMapping.external_source == "danbooru",
        )
    )
    mappings = {m.external_tag: m for m in result.scalars().all()}

    resolved = []

    for sugg in suggestions:
        if "tag_id" in sugg:
            # Already has internal tag_id (from custom model)
            resolved.append(sugg)
            continue

        external_tag = sugg.get("external_tag")
        if not external_tag:
            continue

        mapping = mappings.get(external_tag)
        if not mapping:
            # No mapping found, skip (could log for later manual mapping)
            continue

        if mapping.internal_tag_id is None:
            # Explicitly ignored tag
            continue

        # Create resolved suggestion
        resolved_sugg = sugg.copy()
        resolved_sugg["tag_id"] = mapping.internal_tag_id
        resolved_sugg["confidence"] *= mapping.confidence  # Apply mapping confidence
        del resolved_sugg["external_tag"]  # Remove external_tag key
        resolved.append(resolved_sugg)

    return resolved
```

**Step 3: Integrate into background job**

Update `app/tasks/tag_suggestion_job.py` to call `resolve_external_tags()` before `resolve_tag_relationships()`:

```python
# In generate_tag_suggestions():

# 3.5: Resolve external tags to internal tag IDs (NEW)
from app.services.tag_mapping_service import resolve_external_tags
predictions = await resolve_external_tags(db, predictions)

# 4: Resolve tag relationships (existing)
resolved_predictions = await resolve_tag_relationships(db, predictions)
```

**Step 4: Commit**

```bash
git add app/services/tag_mapping_service.py tests/services/test_tag_mapping_service.py app/tasks/tag_suggestion_job.py
git commit -m "feat: add tag mapping service for Danbooru tags

- Resolve external Danbooru tags to internal tag IDs
- Support ignore mappings (NULL internal_tag_id)
- Apply mapping confidence to suggestions
- Integrate into background job pipeline"
```

---

### Task 2.3: Populate Tag Mappings

**Files:**
- Create: `scripts/import_tag_mappings.py`

**Purpose:** Import Danbooru tag → internal tag mappings from CSV or manual configuration.

**Step 1: Create import script**

```python
# scripts/import_tag_mappings.py

"""
Import Danbooru tag mappings.

Usage:
    uv run python scripts/import_tag_mappings.py mappings.csv
"""

import asyncio
import csv
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.database import get_async_session
from app.models.tag import Tags
from app.models.tag_mapping import TagMapping


async def import_mappings(csv_path: Path) -> None:
    """Import tag mappings from CSV file."""
    async with get_async_session() as db:
        # Read CSV: danbooru_tag,internal_tag_title,action
        # action: "map" (create mapping) or "ignore" (NULL internal_tag_id)
        with open(csv_path) as f:
            reader = csv.DictReader(f)

            for row in reader:
                danbooru_tag = row["danbooru_tag"].strip()
                internal_title = row.get("internal_tag_title", "").strip()
                action = row.get("action", "map").strip()

                # Check if mapping already exists
                result = await db.execute(
                    select(TagMapping).where(
                        TagMapping.external_tag == danbooru_tag,
                        TagMapping.external_source == "danbooru",
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    print(f"Skipping {danbooru_tag}: mapping exists")
                    continue

                internal_tag_id = None
                if action == "map" and internal_title:
                    # Find internal tag by title
                    result = await db.execute(
                        select(Tags).where(Tags.title == internal_title)
                    )
                    tag = result.scalar_one_or_none()
                    if tag:
                        internal_tag_id = tag.tag_id
                    else:
                        print(f"Warning: internal tag '{internal_title}' not found")
                        continue

                # Create mapping
                mapping = TagMapping(
                    external_tag=danbooru_tag,
                    external_source="danbooru",
                    internal_tag_id=internal_tag_id,
                    confidence=1.0,
                )
                db.add(mapping)
                print(f"Created mapping: {danbooru_tag} → {internal_title or 'IGNORE'}")

        await db.commit()
        print("Done!")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run python scripts/import_tag_mappings.py mappings.csv")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)

    asyncio.run(import_mappings(csv_path))
```

**Step 2: Create initial mappings CSV**

```csv
# data/tag_mappings.csv
danbooru_tag,internal_tag_title,action
long_hair,long hair,map
short_hair,short hair,map
blonde_hair,blonde hair,map
blue_eyes,blue eyes,map
smile,smile,map
blush,blush,map
1girl,,ignore
1boy,,ignore
solo,,ignore
```

**Step 3: Run import**

```bash
uv run python scripts/import_tag_mappings.py data/tag_mappings.csv
```

**Step 4: Commit**

```bash
git add scripts/import_tag_mappings.py data/tag_mappings.csv
git commit -m "feat: add tag mapping import script

- Import Danbooru → internal tag mappings from CSV
- Support 'ignore' action for tags we don't want
- Skip existing mappings"
```

---

### Task 2.4: Add Configuration Settings

**Files:**
- Modify: `app/config.py`

**Step 1: Add ML settings**

```python
# app/config.py (add to Settings class)

# ML Model Settings
ML_MODELS_PATH: str = Field(
    default="/shuushuu/ml_models",
    description="Path to ML model files",
)
ML_MIN_CONFIDENCE_DANBOORU: float = Field(
    default=0.35,
    description="Minimum confidence for Danbooru (WD14) predictions",
)
ML_MIN_CONFIDENCE_CUSTOM: float = Field(
    default=0.6,
    description="Minimum confidence for custom theme predictions",
)
```

**Step 2: Commit**

```bash
git add app/config.py
git commit -m "feat: add ML model configuration settings

- ML_MODELS_PATH for model file location
- Separate confidence thresholds for Danbooru and custom models"
```

---

### Task 2.5: Update Tests for Real Models

**Files:**
- Modify: `tests/services/test_ml_service.py`

**Step 1: Add integration test with mock ONNX**

```python
# tests/services/test_ml_service.py (add to existing)

from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_ml_service_with_mocked_onnx():
    """Test ML service with mocked ONNX model."""
    mock_onnx = AsyncMock()
    mock_onnx.predict.return_value = [
        {"tag": "long_hair", "confidence": 0.92},
        {"tag": "blue_eyes", "confidence": 0.88},
    ]

    with patch("app.services.ml_service.ONNXModel") as MockONNX:
        MockONNX.return_value = mock_onnx

        service = MLTagSuggestionService()
        await service.load_models()

        suggestions = await service.generate_suggestions("/path/to/image.jpg")

        assert len(suggestions) == 2
        assert suggestions[0]["external_tag"] == "long_hair"
        assert suggestions[0]["model_source"] == "danbooru"
```

**Step 2: Commit**

```bash
git add tests/services/test_ml_service.py
git commit -m "test: add mocked ONNX tests for ML service

- Test ML service integration with mocked ONNX runtime
- Verify prediction format and confidence filtering"
```

---

## Verification & Next Steps

### Final Verification Checklist

After completing Phase 2 tasks, verify:

- [ ] ONNX model loads without errors
- [ ] WD14 predictions return Danbooru tag strings
- [ ] Tag mappings resolve to internal tag IDs
- [ ] Unmapped tags are skipped or logged
- [ ] End-to-end workflow works with real predictions
- [ ] GPU acceleration works if CUDA available
- [ ] Performance is acceptable (< 1s per image)

### Phase 3: Custom Theme Model (Future)

Train a custom model on your 359 internal tags:

1. **Data preparation** - Export tagged images for training
2. **Model training** - Fine-tune a vision model on your tag vocabulary
3. **ONNX export** - Convert trained model to ONNX format
4. **Integration** - Add custom model alongside WD14
5. **Evaluation** - Compare custom vs WD14 accuracy on your data

### Commands Reference

```bash
# Run all tag suggestion tests
uv run pytest tests/ -k "tag_suggestion" -v

# Run integration tests only
uv run pytest tests/integration/test_tag_suggestion_workflow.py -v

# Start worker (includes ML service)
uv run arq app.tasks.worker.WorkerSettings

# Check migrations
uv run alembic upgrade head

# Import tag mappings
uv run python scripts/import_tag_mappings.py data/tag_mappings.csv
```

---

**Phase 1 Completed:** 2025-12-04

**Phase 2 Status:** Pending - requires WD14 model download and tag mapping data

**Commits:** Phase 1 complete (~15 commits), Phase 2 planned (~5 commits)

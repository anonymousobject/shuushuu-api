"""Integration tests for WDTaggerModel against the real ONNX model.

These tests require the real model file at settings.ML_MODELS_PATH.  When the
model is absent they skip automatically — run with the real models via:

    ML_MODELS_PATH=/home/dtaylor/shuu/ml_models \
        uv run pytest tests/integration/test_wd_tagger_model.py -q
"""

from pathlib import Path

import pytest
from PIL import Image

from app.config import settings

# Project root is parent of app/ — same resolution as MLTagSuggestionService.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# model.onnx: resolve from ML_MODELS_PATH (may be external)
_MODELS_BASE = (
    Path(settings.ML_MODELS_PATH)
    if Path(settings.ML_MODELS_PATH).is_absolute()
    else _PROJECT_ROOT / settings.ML_MODELS_PATH
)
_MODEL_FILE = _MODELS_BASE / "wd-swinv2-tagger-v3" / "model.onnx"

# selected_tags.csv: always use the repo-local copy in ml_models/
_REPO_ML_DIR = _PROJECT_ROOT / "ml_models"
_TAGS_FILE = _REPO_ML_DIR / "wd-swinv2-tagger-v3" / "selected_tags.csv"

_MODEL_AVAILABLE = _MODEL_FILE.exists() and _TAGS_FILE.exists()


@pytest.mark.integration
@pytest.mark.filterwarnings("ignore::UserWarning:onnxruntime")
@pytest.mark.skipif(
    not _MODEL_AVAILABLE,
    reason=f"Real model not found at {_MODEL_FILE} (set ML_MODELS_PATH to the directory containing wd-swinv2-tagger-v3/)",
)
class TestWDTaggerModelReal:
    """Integration tests that run only when the ONNX model file is present."""

    async def test_predict_confidences_in_unit_range(self, tmp_path: Path) -> None:
        """Every returned confidence must be in [0, 1] — sanity check on output range."""
        from app.services.onnx_model import WDTaggerModel

        img_path = tmp_path / "test.png"
        Image.new("RGB", (600, 400), (180, 120, 80)).save(img_path)

        model = WDTaggerModel(
            model_path=str(_MODEL_FILE),
            tags_path=str(_TAGS_FILE),
        )
        await model.load()

        try:
            results = await model.predict(
                str(img_path),
                min_confidence=0.0,
                include_categories=None,  # general-only
            )
            for item in results:
                assert 0.0 <= item["confidence"] <= 1.0, (
                    f"Confidence {item['confidence']!r} for tag {item['tag']!r} is outside [0,1]"
                )
        finally:
            await model.cleanup()

    async def test_double_sigmoid_tripwire(self, tmp_path: Path) -> None:
        """With min_confidence=0.35, fewer than 2000 tags should pass.

        Under the double-sigmoid bug all 10,861 tags pass 0.35 (because
        sigmoid compresses everything above 0.5).  The fix makes this a
        reasonable subset.
        """
        from app.services.onnx_model import GENERAL_CATEGORY, WDTaggerModel

        img_path = tmp_path / "test.png"
        Image.new("RGB", (600, 400), (180, 120, 80)).save(img_path)

        model = WDTaggerModel(
            model_path=str(_MODEL_FILE),
            tags_path=str(_TAGS_FILE),
        )
        await model.load()

        try:
            results = await model.predict(
                str(img_path),
                min_confidence=0.35,
                include_categories={GENERAL_CATEGORY, 4, 9},  # all categories
            )
            count = len(results)
            assert count < 2000, (
                f"Expected fewer than 2000 tags above 0.35 confidence, got {count}.  "
                "This may indicate the double-sigmoid bug was re-introduced."
            )
        finally:
            await model.cleanup()

    async def test_max_confidence_above_threshold(self, tmp_path: Path) -> None:
        """With min_confidence=0.0, at least one tag must exceed 0.3 confidence.

        Guards against degenerate all-zeros output from broken preprocessing.
        """
        from app.services.onnx_model import WDTaggerModel

        img_path = tmp_path / "test.png"
        Image.new("RGB", (600, 400), (180, 120, 80)).save(img_path)

        model = WDTaggerModel(
            model_path=str(_MODEL_FILE),
            tags_path=str(_TAGS_FILE),
        )
        await model.load()

        try:
            results = await model.predict(
                str(img_path),
                min_confidence=0.0,
                include_categories=None,
            )
            max_confidence = max((item["confidence"] for item in results), default=0.0)
            assert max_confidence > 0.3, (
                f"Max confidence is only {max_confidence:.4f} — preprocessing may be broken."
            )
        finally:
            await model.cleanup()

"""
ML Tag Suggestion Service

Supports multiple anime image tagging models:
- WD-Tagger v3 (wd-swinv2-tagger-v3)
- Animetimm models (swinv2_base_window8_256.dbv4-full, etc.)
"""

from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.core.logging import get_logger
from app.services.animetimm_model import AnimetimmModel
from app.services.onnx_model import WDTaggerModel

logger = get_logger(__name__)

# Project root is parent of app/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TaggingModel(Protocol):
    """Protocol for tagging models."""

    async def predict(
        self,
        image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def cleanup(self) -> None: ...


class MLTagSuggestionService:
    """
    ML Tag Suggestion Service

    Supports multiple tagging models configured via ML_MODEL_NAME setting:
    - wd-swinv2-tagger-v3: WD-Tagger v3
    - swinv2_base_window8_256.dbv4-full: Animetimm SwinV2 (newer, more tags)
    - caformer_b36.dbv4-full: Animetimm CAFormer

    Missing model files raise FileNotFoundError; unknown model names raise ValueError.
    """

    def __init__(self) -> None:
        self.model: WDTaggerModel | AnimetimmModel | None = None
        self._model_name = ""

    async def load_models(self) -> None:
        """Load ML model based on configuration."""
        model_path_setting = Path(settings.ML_MODELS_PATH)
        # Resolve relative paths relative to project root
        if not model_path_setting.is_absolute():
            model_dir = PROJECT_ROOT / model_path_setting
        else:
            model_dir = model_path_setting

        model_name = settings.ML_MODEL_NAME
        self._model_name = model_name

        logger.info(
            "ml_service_loading_model",
            model_name=model_name,
            model_dir=str(model_dir),
        )

        if model_name == "wd-swinv2-tagger-v3":
            await self._load_wd_tagger(model_dir)
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

    async def _load_wd_tagger(self, model_dir: Path) -> None:
        """Load WD-Tagger v3 model."""
        model_path = model_dir / "wd-swinv2-tagger-v3" / "model.onnx"
        tags_path = model_dir / "wd-swinv2-tagger-v3" / "selected_tags.csv"

        if not (model_path.exists() and tags_path.exists()):
            raise FileNotFoundError(
                f"ML model files not found: {model_path} "
                "(set ML_MODELS_PATH or download the model "
                "— see ml_models/wd-swinv2-tagger-v3/README.md)"
            )

        logger.info("ml_service_loading_wd_tagger", model_path=str(model_path))
        self.model = WDTaggerModel(str(model_path), str(tags_path))
        await self.model.load()

    async def _load_animetimm(self, model_dir: Path, model_name: str) -> None:
        """Load an animetimm model."""
        model_path = model_dir / model_name / "model.onnx"
        tags_path = model_dir / model_name / "selected_tags.csv"

        if not (model_path.exists() and tags_path.exists()):
            raise FileNotFoundError(
                f"ML model files not found: {model_path} "
                "(set ML_MODELS_PATH or download the model "
                "— see ml_models/wd-swinv2-tagger-v3/README.md)"
            )

        logger.info("ml_service_loading_animetimm", model_path=str(model_path))
        self.model = AnimetimmModel(str(model_path), str(tags_path))
        await self.model.load()

    async def generate_raw_predictions(
        self,
        image_path: str,
        *,
        include_categories: set[int],
        min_confidence: float,
    ) -> list[dict[str, Any]]:
        """Raw predictions across the given categories, for the raw-prediction store.

        Returns the model's external tags with their category for any categories
        requested. This single inference feeds both the raw-prediction store and
        the suggestion pipeline.
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

    @property
    def model_name(self) -> str:
        """Name of the loaded model."""
        return self._model_name

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.model:
            await self.model.cleanup()
        self.model = None

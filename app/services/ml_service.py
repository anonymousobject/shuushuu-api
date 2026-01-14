"""
ML Tag Suggestion Service

Supports multiple anime image tagging models:
- WD-Tagger v3 (wd-swinv2-tagger-v3)
- Animetimm models (swinv2_base_window8_256.dbv4-full, etc.)
"""

import asyncio
import random
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.core.logging import get_logger
from app.services.animetimm_model import CHARACTER_CATEGORY as ANIMETIMM_CHARACTER
from app.services.animetimm_model import GENERAL_CATEGORY as ANIMETIMM_GENERAL
from app.services.animetimm_model import AnimetimmModel
from app.services.onnx_model import CHARACTER_CATEGORY, GENERAL_CATEGORY, WDTaggerModel

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


class MockModel:
    """Mock ML model for testing when real model is unavailable."""

    def __init__(self) -> None:
        # Mock predictions with external tags (Danbooru format)
        self.predictions = [
            ("long_hair", 0.92),
            ("smile", 0.88),
            ("blush", 0.85),
            ("dress", 0.82),
            ("ribbon", 0.78),
            ("blue_eyes", 0.75),
            ("blonde_hair", 0.72),
        ]

    async def predict(
        self,
        _image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Mock prediction - returns hardcoded suggestions."""
        await asyncio.sleep(0.05)  # Simulate inference delay

        results = []
        for tag, base_confidence in self.predictions:
            # Add randomness
            confidence = base_confidence + random.uniform(-0.05, 0.05)
            confidence = max(0.0, min(1.0, confidence))

            if confidence >= min_confidence:
                results.append(
                    {
                        "tag": tag,
                        "confidence": confidence,
                        "category": 0,  # General
                    }
                )

        return results

    async def cleanup(self) -> None:
        """No-op for mock model."""
        pass


class MLTagSuggestionService:
    """
    ML Tag Suggestion Service

    Supports multiple tagging models configured via ML_MODEL_NAME setting:
    - wd-swinv2-tagger-v3: WD-Tagger v3 (default)
    - swinv2_base_window8_256.dbv4-full: Animetimm SwinV2 (newer, more tags)

    Falls back to mock model if configured model is unavailable.
    """

    def __init__(self) -> None:
        self.model: WDTaggerModel | AnimetimmModel | MockModel | None = None
        self._using_mock = False
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
        elif model_name.startswith("swinv2_") or model_name.startswith("convnext"):
            # Animetimm models
            await self._load_animetimm(model_dir, model_name)
        else:
            logger.warning(
                "ml_service_unknown_model_using_mock",
                model_name=model_name,
            )
            self.model = MockModel()
            self._using_mock = True

    async def _load_wd_tagger(self, model_dir: Path) -> None:
        """Load WD-Tagger v3 model."""
        model_path = model_dir / "wd-swinv2-tagger-v3" / "model.onnx"
        tags_path = model_dir / "wd-swinv2-tagger-v3" / "selected_tags.csv"

        logger.info(
            "ml_service_checking_wd_tagger_paths",
            model_exists=model_path.exists(),
            tags_exists=tags_path.exists(),
        )

        if model_path.exists() and tags_path.exists():
            logger.info("ml_service_loading_wd_tagger", model_path=str(model_path))
            self.model = WDTaggerModel(str(model_path), str(tags_path))
            await self.model.load()
            self._using_mock = False
        else:
            logger.warning(
                "ml_service_wd_tagger_not_found_using_mock",
                expected_model=str(model_path),
                expected_tags=str(tags_path),
            )
            self.model = MockModel()
            self._using_mock = True

    async def _load_animetimm(self, model_dir: Path, model_name: str) -> None:
        """Load an animetimm model."""
        model_path = model_dir / model_name / "model.onnx"
        tags_path = model_dir / model_name / "selected_tags.csv"

        logger.info(
            "ml_service_checking_animetimm_paths",
            model_name=model_name,
            model_exists=model_path.exists(),
            tags_exists=tags_path.exists(),
        )

        if model_path.exists() and tags_path.exists():
            logger.info("ml_service_loading_animetimm", model_path=str(model_path))
            self.model = AnimetimmModel(str(model_path), str(tags_path))
            await self.model.load()
            self._using_mock = False
        else:
            logger.warning(
                "ml_service_animetimm_not_found_using_mock",
                expected_model=str(model_path),
                expected_tags=str(tags_path),
            )
            self.model = MockModel()
            self._using_mock = True

    async def generate_suggestions(
        self,
        image_path: str,
        min_confidence: float = 0.35,
    ) -> list[dict[str, Any]]:
        """
        Generate tag suggestions for an image.

        Returns list of dicts with keys:
            - external_tag: Danbooru tag name (e.g., "long_hair")
            - confidence: float 0-1
            - model_source: "danbooru"
            - model_version: model name or "mock"
        """
        if not self.model:
            raise RuntimeError("Models not loaded. Call load_models() first.")

        # Determine which categories to include based on model type
        if isinstance(self.model, AnimetimmModel):
            include_categories = {ANIMETIMM_GENERAL, ANIMETIMM_CHARACTER}
        else:
            include_categories = {GENERAL_CATEGORY, CHARACTER_CATEGORY}

        # Run inference
        predictions = await self.model.predict(
            image_path,
            min_confidence=min_confidence,
            include_categories=include_categories,
        )

        # Format results
        suggestions = []
        for pred in predictions:
            suggestions.append(
                {
                    "external_tag": pred["tag"],
                    "confidence": pred["confidence"],
                    "model_source": "danbooru",
                    "model_version": self._model_name if not self._using_mock else "mock",
                }
            )

        logger.info(
            "ml_service_suggestions_generated",
            image_path=image_path,
            suggestion_count=len(suggestions),
            model_name=self._model_name,
            using_mock=self._using_mock,
        )

        return suggestions

    @property
    def using_mock(self) -> bool:
        """Whether the service is using mock model."""
        return self._using_mock

    @property
    def model_name(self) -> str:
        """Name of the loaded model."""
        return self._model_name

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.model:
            await self.model.cleanup()
        self.model = None

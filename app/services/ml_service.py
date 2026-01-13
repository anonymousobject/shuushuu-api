"""
ML Tag Suggestion Service

Uses WD-Tagger v3 for anime image tagging with Danbooru vocabulary.
"""

import asyncio
import random
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.services.onnx_model import CHARACTER_CATEGORY, GENERAL_CATEGORY, WDTaggerModel

logger = get_logger(__name__)

# Project root is parent of app/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    Uses WD-Tagger v3 for Danbooru tag predictions.
    Falls back to mock model if real model is unavailable.
    """

    def __init__(self) -> None:
        self.danbooru_model: WDTaggerModel | MockModel | None = None
        self._using_mock = False

    async def load_models(self) -> None:
        """Load ML models into memory."""
        model_path_setting = Path(settings.ML_MODELS_PATH)
        # Resolve relative paths relative to project root
        if not model_path_setting.is_absolute():
            model_dir = PROJECT_ROOT / model_path_setting
        else:
            model_dir = model_path_setting
        model_path = model_dir / "wd-swinv2-tagger-v3" / "model.onnx"
        tags_path = model_dir / "wd-swinv2-tagger-v3" / "selected_tags.csv"

        logger.info(
            "ml_service_checking_model_paths",
            model_dir=str(model_dir),
            model_exists=model_path.exists(),
            tags_exists=tags_path.exists(),
        )

        if model_path.exists() and tags_path.exists():
            logger.info(
                "ml_service_loading_wd_tagger",
                model_path=str(model_path),
            )
            self.danbooru_model = WDTaggerModel(str(model_path), str(tags_path))
            await self.danbooru_model.load()
            self._using_mock = False
        else:
            logger.warning(
                "ml_service_model_not_found_using_mock",
                expected_model=str(model_path),
                expected_tags=str(tags_path),
            )
            self.danbooru_model = MockModel()
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
            - model_version: "wd-swinv2-tagger-v3"
        """
        if not self.danbooru_model:
            raise RuntimeError("Models not loaded. Call load_models() first.")

        # Run WD-Tagger inference (include both general and character tags)
        predictions = await self.danbooru_model.predict(
            image_path,
            min_confidence=min_confidence,
            include_categories={GENERAL_CATEGORY, CHARACTER_CATEGORY},
        )

        # Format results
        suggestions = []
        for pred in predictions:
            suggestions.append(
                {
                    "external_tag": pred["tag"],
                    "confidence": pred["confidence"],
                    "model_source": "danbooru",
                    "model_version": "wd-swinv2-tagger-v3" if not self._using_mock else "mock",
                }
            )

        logger.info(
            "ml_service_suggestions_generated",
            image_path=image_path,
            suggestion_count=len(suggestions),
            using_mock=self._using_mock,
        )

        return suggestions

    @property
    def using_mock(self) -> bool:
        """Whether the service is using mock model."""
        return self._using_mock

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.danbooru_model:
            await self.danbooru_model.cleanup()
        self.danbooru_model = None

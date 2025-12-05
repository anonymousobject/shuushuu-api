"""
ML Tag Suggestion Service

PHASE 1: Mock implementation that returns hardcoded suggestions.
PHASE 2: Replace with real model inference.
"""

import asyncio
import random
from typing import List, Dict


class MockModel:
    """Mock ML model for testing"""

    def __init__(self, model_type: str):
        self.model_type = model_type

        # Mock tag predictions (tag_id → confidence)
        if model_type == "custom_theme":
            self.predictions = {
                46: 0.92,   # long hair
                161: 0.88,  # short hair
                25: 0.85,   # blush
                3: 0.75,    # ribbon
                143: 0.82,  # smile
            }
        else:  # danbooru
            self.predictions = {
                46: 0.90,   # long hair (overlap with custom)
                12525: 0.87,  # blue eyes
                169: 0.78,    # blonde hair
            }

    async def predict(self, image_path: str) -> List[Dict]:
        """Mock prediction - returns hardcoded suggestions"""
        # Simulate inference delay
        await asyncio.sleep(0.1)

        # Add some randomness to confidence
        predictions = []
        for tag_id, base_confidence in self.predictions.items():
            confidence = base_confidence + random.uniform(-0.05, 0.05)
            confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]

            predictions.append({
                "tag_id": tag_id,
                "confidence": confidence,
                "model_source": self.model_type
            })

        return predictions


class MLTagSuggestionService:
    """
    ML Tag Suggestion Service

    PHASE 1: Uses mock models
    PHASE 2: Replace with real ONNX/PyTorch models
    """

    def __init__(self):
        self.custom_model = None
        self.danbooru_model = None

    async def load_models(self):
        """Load ML models into memory"""
        # PHASE 1: Load mock models
        self.custom_model = MockModel("custom_theme")
        self.danbooru_model = MockModel("danbooru")

        # PHASE 2: Load real models
        # self.custom_model = ONNXModel("/path/to/custom_theme.onnx")
        # self.danbooru_model = ONNXModel("/path/to/danbooru.onnx")

    async def generate_suggestions(
        self,
        image_path: str,
        min_confidence: float = 0.6
    ) -> List[Dict]:
        """
        Generate tag suggestions for an image.

        Returns list of dicts with keys: tag_id, confidence, model_source
        """
        if not self.custom_model or not self.danbooru_model:
            raise RuntimeError("Models not loaded. Call load_models() first.")

        # Run both models in parallel
        custom_preds, danbooru_preds = await asyncio.gather(
            self.custom_model.predict(image_path),
            self.danbooru_model.predict(image_path)
        )

        # Merge predictions (prioritize custom for themes)
        merged = self._merge_predictions(custom_preds, danbooru_preds)

        # Filter by confidence
        filtered = [
            pred for pred in merged
            if pred["confidence"] >= min_confidence
        ]

        return filtered

    def _merge_predictions(
        self,
        custom_preds: List[Dict],
        danbooru_preds: List[Dict]
    ) -> List[Dict]:
        """
        Merge predictions from both models.

        Strategy: Prioritize custom model predictions, use Danbooru for additional tags.
        """
        # Use dict to deduplicate by tag_id
        merged = {}

        # Add custom predictions first (higher priority)
        for pred in custom_preds:
            merged[pred["tag_id"]] = pred

        # Add Danbooru predictions if not already present
        for pred in danbooru_preds:
            if pred["tag_id"] not in merged:
                merged[pred["tag_id"]] = pred

        return list(merged.values())

    async def cleanup(self):
        """Cleanup resources"""
        self.custom_model = None
        self.danbooru_model = None

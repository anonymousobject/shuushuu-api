"""
Animetimm Model Inference Wrapper

Provides inference for animetimm models (swinv2, convnext, etc.) trained on Danbooru v4.
These models use ImageNet-style preprocessing and support per-tag thresholds.
"""

import asyncio
import csv
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]

from app.config import settings
from app.core.logging import get_logger
from app.services.animetimm_preprocess import (
    apply_test_pipeline,
    load_rgb,
    load_test_pipeline,
)
from app.services.onnx_providers import make_session_options, select_providers

logger = get_logger(__name__)

# Category IDs (same as WD-Tagger)
GENERAL_CATEGORY = 0
CHARACTER_CATEGORY = 4
RATING_CATEGORY = 9


class AnimetimmModel:
    """
    Animetimm ONNX model wrapper.

    Supports models from https://huggingface.co/animetimm trained on Danbooru v4.
    Features per-tag thresholds for optimal F1 scores.
    """

    def __init__(self, model_path: str, tags_path: str):
        self.model_path = Path(model_path)
        self.tags_path = Path(tags_path)
        self.session: ort.InferenceSession | None = None
        self.tag_names: list[str] = []
        self.tag_categories: list[int] = []
        self.tag_thresholds: list[float] = []  # Per-tag best thresholds
        self.input_name: str = ""
        self.preprocess_pipeline: list[dict[str, Any]] = []

    async def load(self) -> None:
        """Load model into memory (runs in thread pool)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self) -> None:
        """Synchronous model loading."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found: {self.model_path}")

        # Prefer GPU (CUDA/ROCm) when the installed onnxruntime build exposes
        # it, else CPU. Requesting only available providers avoids spurious
        # "provider not available" warnings.
        providers = select_providers(ort.get_available_providers())
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=make_session_options(settings.ML_INTRA_OP_THREADS),
            providers=providers,
        )

        active_providers = self.session.get_providers()
        logger.info(
            "animetimm_model_loaded",
            model_path=str(self.model_path),
            providers=active_providers,
        )

        # Get input name
        self.input_name = self.session.get_inputs()[0].name

        # Load tag vocabulary with thresholds
        self._load_tags()

        # Load this model's preprocessing pipeline (preprocess.json), so any
        # animetimm model — at any resolution/normalization — is a drop-in.
        self.preprocess_pipeline = load_test_pipeline(self.model_path.parent)

    def _load_tags(self) -> None:
        """Load tag names, categories, and per-tag thresholds from CSV."""
        if not self.tags_path.exists():
            raise FileNotFoundError(f"Tags file not found: {self.tags_path}")

        with open(self.tags_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.tag_names.append(row["name"])
                self.tag_categories.append(int(row["category"]))
                # Use per-tag best_threshold if available, default to 0.35
                threshold = float(row.get("best_threshold", 0.35))
                self.tag_thresholds.append(threshold)

        logger.info(
            "animetimm_tags_loaded",
            total_tags=len(self.tag_names),
            has_thresholds=any(t != 0.35 for t in self.tag_thresholds),
        )

    async def predict(
        self,
        image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
        use_per_tag_thresholds: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Run inference on an image.

        Args:
            image_path: Path to image file
            min_confidence: Minimum confidence threshold (used as floor even with per-tag)
            include_categories: Set of category IDs to include (default: general only)
            use_per_tag_thresholds: Use per-tag optimal thresholds instead of global

        Returns:
            List of dicts: {tag: str, confidence: float, category: int}
        """
        if include_categories is None:
            include_categories = {GENERAL_CATEGORY}

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._predict_sync,
            image_path,
            min_confidence,
            include_categories,
            use_per_tag_thresholds,
        )

    def _predict_sync(
        self,
        image_path: str,
        min_confidence: float,
        include_categories: set[int],
        use_per_tag_thresholds: bool,
    ) -> list[dict[str, Any]]:
        """Synchronous prediction."""
        if not self.session:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Preprocess image
        img_array = self._preprocess_image(image_path)

        # Run inference - use 'prediction' output (sigmoid already applied)
        outputs = self.session.run(["prediction"], {self.input_name: img_array})
        probabilities = outputs[0][0]  # Remove batch dimension

        # Build results
        results: list[dict[str, Any]] = []
        for i, (name, category, prob) in enumerate(
            zip(self.tag_names, self.tag_categories, probabilities, strict=True)
        ):
            if category not in include_categories:
                continue

            # Determine threshold
            if use_per_tag_thresholds:
                threshold = max(self.tag_thresholds[i], min_confidence)
            else:
                threshold = min_confidence

            if prob < threshold:
                continue

            results.append(
                {
                    "tag": name,
                    "confidence": float(prob),
                    "category": category,
                }
            )

        # Sort by confidence descending
        results.sort(key=lambda x: float(x["confidence"]), reverse=True)

        return results

    def _preprocess_image(self, image_path: str) -> np.ndarray[Any, np.dtype[np.float32]]:
        """Preprocess an image using this model's preprocess.json pipeline."""
        return apply_test_pipeline(load_rgb(image_path), self.preprocess_pipeline)

    async def cleanup(self) -> None:
        """Release resources."""
        self.session = None
        self.tag_names = []
        self.tag_categories = []
        self.tag_thresholds = []

"""
ONNX Model Inference Wrapper

Provides GPU-accelerated inference for WD-Tagger v3 model.
"""

import asyncio
import csv
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from PIL import Image

from app.core.logging import get_logger

logger = get_logger(__name__)

# WD-Tagger v3 constants
INPUT_SIZE = 448
GENERAL_CATEGORY = 0  # General tags (themes, attributes)
CHARACTER_CATEGORY = 4  # Character tags
RATING_CATEGORY = 9  # Rating tags


class WDTaggerModel:
    """
    WD-Tagger v3 ONNX model wrapper.

    Specialized for anime image tagging with Danbooru vocabulary.
    """

    def __init__(self, model_path: str, tags_path: str):
        self.model_path = Path(model_path)
        self.tags_path = Path(tags_path)
        self.session: ort.InferenceSession | None = None
        self.tag_names: list[str] = []
        self.tag_categories: list[int] = []
        self.input_name: str = ""

    async def load(self) -> None:
        """Load model into memory (runs in thread pool)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self) -> None:
        """Synchronous model loading."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found: {self.model_path}")

        # Prefer GPU if available
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)

        active_providers = self.session.get_providers()
        logger.info(
            "wd_tagger_model_loaded",
            model_path=str(self.model_path),
            providers=active_providers,
        )

        # Get input name
        self.input_name = self.session.get_inputs()[0].name

        # Load tag vocabulary
        self._load_tags()

    def _load_tags(self) -> None:
        """Load tag names and categories from CSV."""
        if not self.tags_path.exists():
            raise FileNotFoundError(f"Tags file not found: {self.tags_path}")

        with open(self.tags_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.tag_names.append(row["name"])
                self.tag_categories.append(int(row["category"]))

        logger.info(
            "wd_tagger_tags_loaded",
            total_tags=len(self.tag_names),
        )

    async def predict(
        self,
        image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run inference on an image.

        Args:
            image_path: Path to image file
            min_confidence: Minimum confidence threshold
            include_categories: Set of category IDs to include (default: general only)

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
        )

    def _predict_sync(
        self,
        image_path: str,
        min_confidence: float,
        include_categories: set[int],
    ) -> list[dict[str, Any]]:
        """Synchronous prediction."""
        if not self.session:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Preprocess image
        img_array = self._preprocess_image(image_path)

        # Run inference
        output_name = self.session.get_outputs()[0].name
        outputs = self.session.run([output_name], {self.input_name: img_array})
        raw_predictions = outputs[0][0]  # Remove batch dimension

        # Apply sigmoid to get probabilities
        probabilities = 1 / (1 + np.exp(-raw_predictions))

        # Build results
        results: list[dict[str, Any]] = []
        for name, category, prob in zip(
            self.tag_names, self.tag_categories, probabilities, strict=True
        ):
            if category not in include_categories:
                continue
            if prob < min_confidence:
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

    def _preprocess_image(self, image_path: str) -> "np.ndarray[Any, np.dtype[np.float32]]":
        """
        Preprocess image for WD-Tagger v3.

        - Handle transparency (composite onto white)
        - Pad to square
        - Resize to 448x448
        - Convert RGB to BGR
        - Keep 0-255 range (no normalization)
        - Format: NHWC
        """
        img = Image.open(image_path).convert("RGBA")

        # Composite alpha onto white background
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.paste(img, mask=img.split()[3] if len(img.split()) == 4 else None)
        img = background.convert("RGB")

        # Pad to square
        max_dim = max(img.size)
        pad_left = (max_dim - img.size[0]) // 2
        pad_top = (max_dim - img.size[1]) // 2

        padded = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
        padded.paste(img, (pad_left, pad_top))

        # Resize to model input size
        img = padded.resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BICUBIC)

        # Convert to numpy array (keep 0-255 range, no normalization)
        img_array = np.asarray(img, dtype=np.float32)

        # Convert RGB to BGR (required by WD-Tagger ONNX)
        img_array = img_array[:, :, ::-1]

        # Add batch dimension: (H, W, C) -> (1, H, W, C)
        img_array = np.expand_dims(img_array, axis=0)

        return img_array

    async def cleanup(self) -> None:
        """Release resources."""
        self.session = None
        self.tag_names = []
        self.tag_categories = []

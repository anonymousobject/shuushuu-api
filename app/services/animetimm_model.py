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
from PIL import Image

from app.core.logging import get_logger

logger = get_logger(__name__)

# Animetimm model constants
PAD_SIZE = 512  # Pad to this size first
INPUT_SIZE = 448  # Final input size after resize and crop

# ImageNet normalization values
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

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
            "animetimm_model_loaded",
            model_path=str(self.model_path),
            providers=active_providers,
        )

        # Get input name
        self.input_name = self.session.get_inputs()[0].name

        # Load tag vocabulary with thresholds
        self._load_tags()

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

    def _preprocess_image(self, image_path: str) -> "np.ndarray[Any, np.dtype[np.float32]]":
        """
        Preprocess image for animetimm models.

        Based on preprocess.json:
        1. Pad to 512x512 with white background (bilinear)
        2. Resize to 448 (bicubic with antialiasing)
        3. Center crop to 448x448
        4. Convert to tensor (0-1 range)
        5. Normalize with ImageNet mean/std
        6. Format: NCHW (batch, channels, height, width)
        """
        img = Image.open(image_path).convert("RGBA")

        # Composite alpha onto white background
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.paste(img, mask=img.split()[3] if len(img.split()) == 4 else None)
        img = background.convert("RGB")

        # Step 1: Pad to 512x512 with white background (maintaining aspect ratio)
        # Calculate scale to fit within PAD_SIZE
        scale = PAD_SIZE / max(img.size)
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        img = img.resize(new_size, Image.Resampling.BILINEAR)

        # Center on white canvas
        padded = Image.new("RGB", (PAD_SIZE, PAD_SIZE), (255, 255, 255))
        paste_x = (PAD_SIZE - img.size[0]) // 2
        paste_y = (PAD_SIZE - img.size[1]) // 2
        padded.paste(img, (paste_x, paste_y))

        # Step 2: Resize to 448 (bicubic)
        img = padded.resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BICUBIC)

        # Step 3: Center crop (already 448x448, so no-op)

        # Step 4: Convert to numpy array and normalize to 0-1
        img_array = np.asarray(img, dtype=np.float32) / 255.0

        # Step 5: Apply ImageNet normalization
        img_array = (img_array - IMAGENET_MEAN) / IMAGENET_STD

        # Step 6: Convert HWC to CHW format
        img_array = np.transpose(img_array, (2, 0, 1))

        # Add batch dimension: (C, H, W) -> (1, C, H, W)
        img_array = np.expand_dims(img_array, axis=0)

        return img_array

    async def cleanup(self) -> None:
        """Release resources."""
        self.session = None
        self.tag_names = []
        self.tag_categories = []
        self.tag_thresholds = []

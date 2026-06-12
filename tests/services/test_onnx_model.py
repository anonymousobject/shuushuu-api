"""Unit tests for WDTaggerModel preprocessing.

These tests exercise _preprocess_image on an unloaded model instance — no
ONNX session or model file is needed.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services.onnx_model import WDTaggerModel


@pytest.mark.unit
class TestWDTaggerPreprocessing:
    """Tests for WDTaggerModel._preprocess_image."""

    def _make_model(self) -> WDTaggerModel:
        """Return an unloaded WDTaggerModel (no real files needed)."""
        return WDTaggerModel(model_path="/nonexistent/model.onnx", tags_path="/nonexistent/tags.csv")

    def test_output_shape(self, tmp_path: Path) -> None:
        """Output array must have NHWC shape (1, 448, 448, 3)."""
        img_path = tmp_path / "test.png"
        Image.new("RGBA", (200, 150), (128, 64, 32, 200)).save(img_path)

        model = self._make_model()
        result = model._preprocess_image(str(img_path))

        assert result.shape == (1, 448, 448, 3)

    def test_output_dtype_float32(self, tmp_path: Path) -> None:
        """Output must be float32."""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (300, 300), (100, 100, 100)).save(img_path)

        model = self._make_model()
        result = model._preprocess_image(str(img_path))

        assert result.dtype == np.float32

    def test_value_range_0_255(self, tmp_path: Path) -> None:
        """Pixel values must stay in [0, 255] (no normalization)."""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (448, 448), (200, 100, 50)).save(img_path)

        model = self._make_model()
        result = model._preprocess_image(str(img_path))

        assert float(result.min()) >= 0.0
        assert float(result.max()) <= 255.0

    def test_bgr_channel_order_solid_red(self, tmp_path: Path) -> None:
        """Solid red RGB image must have near-zero blue channel (index 0) and
        near-255 red channel (index 2) after BGR conversion."""
        img_path = tmp_path / "red.png"
        # Solid red: R=255, G=0, B=0
        Image.new("RGB", (448, 448), (255, 0, 0)).save(img_path)

        model = self._make_model()
        result = model._preprocess_image(str(img_path))

        # After RGB→BGR: channel 0 = blue ≈ 0, channel 2 = red ≈ 255
        center_pixel = result[0, 224, 224, :]  # batch=0, center pixel, all channels
        assert center_pixel[0] < 10.0, f"Expected blue≈0 at channel 0, got {center_pixel[0]}"
        assert center_pixel[2] > 245.0, f"Expected red≈255 at channel 2, got {center_pixel[2]}"

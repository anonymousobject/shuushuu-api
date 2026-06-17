"""Tests for preprocess.json-driven animetimm preprocessing.

The key guarantee: the default pipeline (swinv2_base_window8_256.dbv4-full) must
be numerically identical to the legacy hardcoded preprocessing, so the ~1M-image
backfill (run with the hardcoded path) stays consistent with new-upload inference
once we switch to the json-driven path.
"""

import numpy as np
import pytest
from PIL import Image

from app.services.animetimm_preprocess import (
    DEFAULT_TEST_PIPELINE,
    apply_test_pipeline,
    load_rgb,
)


def _legacy_hardcoded(path: str) -> np.ndarray:
    """The pre-refactor AnimetimmModel._preprocess_image, inlined as the golden."""
    pad, inp = 512, 448
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    img = bg.convert("RGB")
    scale = pad / max(img.size)
    new = (int(img.size[0] * scale), int(img.size[1] * scale))
    img = img.resize(new, Image.Resampling.BILINEAR)
    padded = Image.new("RGB", (pad, pad), (255, 255, 255))
    padded.paste(img, ((pad - img.size[0]) // 2, (pad - img.size[1]) // 2))
    img = padded.resize((inp, inp), Image.Resampling.BICUBIC)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - mean) / std
    arr = np.transpose(arr, (2, 0, 1))
    return np.expand_dims(arr, axis=0)


def test_default_pipeline_matches_legacy(tmp_path):
    """Non-square RGBA image: json-driven default == legacy hardcoded, bit-for-bit."""
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 256, (300, 200, 4), dtype=np.uint8)  # RGBA, non-square
    p = tmp_path / "x.png"
    Image.fromarray(arr, "RGBA").save(p)

    got = apply_test_pipeline(load_rgb(str(p)), DEFAULT_TEST_PIPELINE)
    want = _legacy_hardcoded(str(p))

    assert got.shape == want.shape == (1, 3, 448, 448)
    assert np.allclose(got, want, atol=1e-6)


def test_unknown_op_raises(tmp_path):
    img = Image.new("RGB", (10, 10), (128, 128, 128))
    with pytest.raises(ValueError, match="unsupported preprocess op"):
        apply_test_pipeline(img, [{"type": "rotate", "degrees": 90}])


def test_respects_pipeline_resolution(tmp_path):
    """A 384-res pipeline (like caformer_b36) yields a 384x384 tensor."""
    img = Image.new("RGB", (640, 480), (10, 20, 30))
    pipeline = [
        {"type": "pad_to_size", "size": [512, 512], "background_color": "white",
         "interpolation": "bilinear"},
        {"type": "resize", "size": 384, "interpolation": "bicubic"},
        {"type": "center_crop", "size": [384, 384]},
        {"type": "maybe_to_tensor"},
        {"type": "normalize", "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5]},
    ]
    out = apply_test_pipeline(img, pipeline)
    assert out.shape == (1, 3, 384, 384)

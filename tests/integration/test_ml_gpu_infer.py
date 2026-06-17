"""Drift guard for the standalone GPU-host runner (scripts/ml_gpu_infer.py).

scripts/ml_gpu_infer.py copies the animetimm preprocessing and themes-only
selection so it can run app-free on a GPU host. These tests prove the copy
stays numerically identical to the canonical app code:

- test_preprocess_matches_canonical: exact, content-independent, always runs —
  the highest-drift-risk piece (preprocessing math) compared tensor-for-tensor.
- test_predict_matches_canonical: end-to-end via subprocess vs AnimetimmModel,
  skipped unless the real model is present (set ML_MODELS_PATH).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
_RUNNER = _REPO / "scripts" / "ml_gpu_infer.py"

# Load the standalone script as a module (it is not an importable package).
_spec = importlib.util.spec_from_file_location("ml_gpu_infer", _RUNNER)
assert _spec is not None and _spec.loader is not None
ml_gpu_infer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml_gpu_infer)

_MODEL_DIR = Path(os.environ.get("ML_MODELS_PATH", "ml_models")) / "swinv2_base_window8_256.dbv4-full"


@pytest.mark.unit
def test_standalone_preprocess_matches_canonical(tmp_path):
    """The runner's app-free preprocessing copy must match the canonical app code."""
    from app.services import animetimm_preprocess as canonical

    # The default pipelines (constants) must not drift between copy and canonical.
    assert ml_gpu_infer.DEFAULT_TEST_PIPELINE == canonical.DEFAULT_TEST_PIPELINE

    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (300, 200, 3), dtype=np.uint8)
    img_path = tmp_path / "img.png"
    Image.fromarray(arr).save(img_path)

    mine = ml_gpu_infer.apply_test_pipeline(
        ml_gpu_infer.load_rgb(str(img_path)), ml_gpu_infer.DEFAULT_TEST_PIPELINE
    )
    want = canonical.apply_test_pipeline(
        canonical.load_rgb(str(img_path)), canonical.DEFAULT_TEST_PIPELINE
    )
    assert mine.shape == want.shape == (1, 3, 448, 448)
    assert np.allclose(mine, want, atol=1e-7)


@pytest.mark.skipif(
    not (_MODEL_DIR / "model.onnx").exists(),
    reason="animetimm model not present; set ML_MODELS_PATH to the dir containing it",
)
async def test_predict_matches_canonical(tmp_path):
    """Running the standalone CLI yields the same selection as AnimetimmModel."""
    from app.services.animetimm_model import GENERAL_CATEGORY, AnimetimmModel

    storage = tmp_path / "storage"
    (storage / "fullsize").mkdir(parents=True)
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, (400, 300, 3), dtype=np.uint8)
    img_path = storage / "fullsize" / "testimg.png"
    Image.fromarray(arr).save(img_path)

    manifest = tmp_path / "m.jsonl"
    manifest.write_text(json.dumps({"image_id": 123, "filename": "testimg", "ext": "png"}) + "\n")
    out = tmp_path / "r.jsonl"

    subprocess.run(
        [
            sys.executable, str(_RUNNER),
            "--manifest", str(manifest),
            "--out", str(out),
            "--model-dir", str(_MODEL_DIR),
            "--storage-path", str(storage),
            "--variant", "fullsize",
            "--min-confidence", "0.35",
            "--no-include-character",  # compare general-only against canonical
        ],
        check=True,
        capture_output=True,
    )

    results = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(results) == 1
    assert results[0]["image_id"] == 123
    standalone = {p["external_tag"]: p["confidence"] for p in results[0]["predictions"]}

    model = AnimetimmModel(str(_MODEL_DIR / "model.onnx"), str(_MODEL_DIR / "selected_tags.csv"))
    await model.load()
    try:
        canon_preds = await model.predict(
            str(img_path), min_confidence=0.35, include_categories={GENERAL_CATEGORY}
        )
    finally:
        await model.cleanup()
    canonical = {c["tag"]: c["confidence"] for c in canon_preds}

    assert set(standalone) == set(canonical)
    for tag, conf in canonical.items():
        assert abs(standalone[tag] - conf) < 1e-4


@pytest.mark.skipif(
    not (_MODEL_DIR / "model.onnx").exists(),
    reason="animetimm model not present; set ML_MODELS_PATH to the dir containing it",
)
def test_corrupt_image_is_skipped_not_fatal(tmp_path):
    """A corrupt/unreadable image is logged and skipped; the run still completes.

    Regression for the backfill crash on a corrupt fullsize .png — one bad file
    must not abort a million-image run.
    """
    storage = tmp_path / "storage"
    (storage / "fullsize").mkdir(parents=True)
    rng = np.random.default_rng(11)
    Image.fromarray(rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)).save(
        storage / "fullsize" / "good.png"
    )
    # Not a valid image — PIL raises UnidentifiedImageError on open.
    (storage / "fullsize" / "bad.png").write_bytes(b"this is not a PNG")

    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"image_id": 1, "filename": "bad", "ext": "png"}) + "\n"
        + json.dumps({"image_id": 2, "filename": "good", "ext": "png"}) + "\n"
    )
    out = tmp_path / "r.jsonl"

    proc = subprocess.run(
        [
            sys.executable, str(_RUNNER),
            "--manifest", str(manifest), "--out", str(out),
            "--model-dir", str(_MODEL_DIR), "--storage-path", str(storage),
            "--variant", "fullsize", "--min-confidence", "0.35",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    ids = {json.loads(line)["image_id"] for line in out.read_text().splitlines() if line.strip()}
    assert ids == {2}  # good image processed, corrupt one skipped
    assert "skipping image 1" in proc.stdout


# ---------------------------------------------------------------------------
# Synthetic predict() tests — no real model or GPU required
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal ONNX session stub for predict() unit tests."""

    def __init__(self, probabilities: list[float]) -> None:
        # probabilities must align with the tag lists passed to predict()
        self._probs = np.array(probabilities, dtype=np.float32)

    def run(self, outputs, feed):  # noqa: ANN001,ANN201
        # predict() does session.run(["prediction"], {...})[0][0]
        return [np.array([self._probs])]


def _make_fake_pipeline_patches(monkeypatch):
    """Patch load_rgb and apply_test_pipeline so predict() needs no real image."""
    dummy_img = object()
    monkeypatch.setattr(ml_gpu_infer, "load_rgb", lambda path: dummy_img)
    monkeypatch.setattr(
        ml_gpu_infer,
        "apply_test_pipeline",
        lambda img, pipeline: np.zeros((1, 3, 448, 448), dtype=np.float32),
    )


# Tags layout used by the synthetic tests:
#   index 0: general tag "sky",       category=0 (GENERAL), threshold=0.35, prob=0.9
#   index 1: rating tag "safe",       category=9 (rating),  threshold=0.35, prob=0.8
#   index 2: character tag "reimu",   category=4 (CHARACTER), threshold=0.35, prob=0.7
#   index 3: general tag "outdoors",  category=0 (GENERAL), threshold=0.35, prob=0.5

_NAMES      = ["sky",  "safe", "reimu",   "outdoors"]
_CATEGORIES = [0,      9,      4,         0         ]
_THRESHOLDS = [0.35,   0.35,   0.35,      0.35      ]
_PROBS      = [0.9,    0.8,    0.7,       0.5       ]

DUMMY_PIPELINE: list = []


@pytest.mark.unit
def test_predict_include_character_emits_general_and_character(monkeypatch, tmp_path):
    """With allowed_categories={GENERAL, CHARACTER}, predictions for both categories appear."""
    _make_fake_pipeline_patches(monkeypatch)

    session = _FakeSession(_PROBS)
    results = ml_gpu_infer.predict(
        session=session,
        input_name="input",
        names=_NAMES,
        categories=_CATEGORIES,
        thresholds=_THRESHOLDS,
        pipeline=DUMMY_PIPELINE,
        image_path=str(tmp_path / "fake.png"),
        min_confidence=0.35,
        model_version="test-model",
        allowed_categories={ml_gpu_infer.GENERAL_CATEGORY, ml_gpu_infer.CHARACTER_CATEGORY},
    )

    emitted_tags = {r["external_tag"] for r in results}
    emitted_categories = {r["category"] for r in results}

    # General tags present
    assert "sky" in emitted_tags
    assert "outdoors" in emitted_tags
    # Character tag present
    assert "reimu" in emitted_tags
    # Rating tag (category 9) not present
    assert "safe" not in emitted_tags

    assert ml_gpu_infer.GENERAL_CATEGORY in emitted_categories
    assert ml_gpu_infer.CHARACTER_CATEGORY in emitted_categories

    # Each result carries a category key
    for r in results:
        assert "category" in r


@pytest.mark.unit
def test_predict_no_include_character_emits_general_only(monkeypatch, tmp_path):
    """With allowed_categories={GENERAL} only, character predictions are suppressed."""
    _make_fake_pipeline_patches(monkeypatch)

    session = _FakeSession(_PROBS)
    results = ml_gpu_infer.predict(
        session=session,
        input_name="input",
        names=_NAMES,
        categories=_CATEGORIES,
        thresholds=_THRESHOLDS,
        pipeline=DUMMY_PIPELINE,
        image_path=str(tmp_path / "fake.png"),
        min_confidence=0.35,
        model_version="test-model",
        allowed_categories={ml_gpu_infer.GENERAL_CATEGORY},
    )

    emitted_tags = {r["external_tag"] for r in results}
    emitted_categories = {r["category"] for r in results}

    assert "sky" in emitted_tags
    assert "outdoors" in emitted_tags
    assert "reimu" not in emitted_tags   # character tag suppressed
    assert "safe" not in emitted_tags    # rating tag suppressed

    assert emitted_categories == {ml_gpu_infer.GENERAL_CATEGORY}

    for r in results:
        assert "category" in r


@pytest.mark.unit
def test_help_shows_include_character_flag():
    """--help output must advertise --include-character / --no-include-character."""
    result = subprocess.run(
        [sys.executable, str(_RUNNER), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--include-character" in result.stdout
    assert "--no-include-character" in result.stdout

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
def test_preprocess_matches_canonical(tmp_path):
    """The copied preprocessing must be tensor-identical to AnimetimmModel."""
    from app.services.animetimm_model import AnimetimmModel

    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (300, 200, 3), dtype=np.uint8)
    img_path = tmp_path / "img.png"
    Image.fromarray(arr).save(img_path)

    canonical = AnimetimmModel("unused", "unused")._preprocess_image(str(img_path))
    mine = ml_gpu_infer.preprocess(str(img_path))

    assert mine.shape == canonical.shape == (1, 3, 448, 448)
    assert np.allclose(mine, canonical, atol=1e-6)


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

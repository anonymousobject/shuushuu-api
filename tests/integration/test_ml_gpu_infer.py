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

_MODEL_DIR = (
    Path(os.environ.get("ML_MODELS_PATH", "ml_models")) / "swinv2_base_window8_256.dbv4-full"
)


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

    # A NON-SQUARE pipeline: the default pipeline is square, which hides any
    # [h, w] vs [w, h] disagreement in pad_to_size/resize/center_crop. Drive a
    # non-square pipeline through both copies so the twin cannot diverge on the
    # width/height axis silently.
    nonsquare_pipeline = [
        {
            "type": "pad_to_size",
            "size": [400, 512],
            "background_color": "white",
            "interpolation": "bilinear",
        },
        {"type": "resize", "size": [200, 256], "interpolation": "bicubic"},
        {"type": "center_crop", "size": [128, 192]},
        {"type": "maybe_to_tensor"},
        {"type": "normalize", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
    ]
    mine_ns = ml_gpu_infer.apply_test_pipeline(
        ml_gpu_infer.load_rgb(str(img_path)), nonsquare_pipeline
    )
    want_ns = canonical.apply_test_pipeline(canonical.load_rgb(str(img_path)), nonsquare_pipeline)
    assert mine_ns.shape == want_ns.shape == (1, 3, 128, 192)  # H=128, W=192
    assert np.allclose(mine_ns, want_ns, atol=1e-7)


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
            sys.executable,
            str(_RUNNER),
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--model-dir",
            str(_MODEL_DIR),
            "--storage-path",
            str(storage),
            "--variant",
            "fullsize",
            "--min-confidence",
            "0.35",
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
        json.dumps({"image_id": 1, "filename": "bad", "ext": "png"})
        + "\n"
        + json.dumps({"image_id": 2, "filename": "good", "ext": "png"})
        + "\n"
    )
    out = tmp_path / "r.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--model-dir",
            str(_MODEL_DIR),
            "--storage-path",
            str(storage),
            "--variant",
            "fullsize",
            "--min-confidence",
            "0.35",
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


# Tags layout used by the synthetic tests:
#   index 0: general tag "sky",       category=0 (GENERAL), threshold=0.35, prob=0.9
#   index 1: rating tag "safe",       category=9 (rating),  threshold=0.35, prob=0.8
#   index 2: character tag "reimu",   category=4 (CHARACTER), threshold=0.35, prob=0.7
#   index 3: general tag "outdoors",  category=0 (GENERAL), threshold=0.35, prob=0.5

_NAMES = ["sky", "safe", "reimu", "outdoors"]
_CATEGORIES = [0, 9, 4, 0]
_THRESHOLDS = [0.35, 0.35, 0.35, 0.35]
_PROBS = [0.9, 0.8, 0.7, 0.5]

DUMMY_PIPELINE: list = []


@pytest.mark.unit
def test_select_predictions_include_character_emits_general_and_character():
    """With allowed_categories={GENERAL, CHARACTER}, predictions for both categories appear."""
    results = ml_gpu_infer.select_predictions(
        np.array(_PROBS, dtype=np.float32),
        _NAMES,
        _CATEGORIES,
        _THRESHOLDS,
        0.35,
        "test-model",
        {ml_gpu_infer.GENERAL_CATEGORY, ml_gpu_infer.CHARACTER_CATEGORY},
    )

    emitted_tags = [r["external_tag"] for r in results]
    assert emitted_tags == ["sky", "reimu", "outdoors"], "sorted by confidence, no rating tag"
    assert {r["category"] for r in results} == {
        ml_gpu_infer.GENERAL_CATEGORY,
        ml_gpu_infer.CHARACTER_CATEGORY,
    }
    assert all(r["model_version"] == "test-model" for r in results)


@pytest.mark.unit
def test_select_predictions_general_only_suppresses_character():
    results = ml_gpu_infer.select_predictions(
        np.array(_PROBS, dtype=np.float32),
        _NAMES,
        _CATEGORIES,
        _THRESHOLDS,
        0.35,
        "test-model",
        {ml_gpu_infer.GENERAL_CATEGORY},
    )

    assert [r["external_tag"] for r in results] == ["sky", "outdoors"]
    assert {r["category"] for r in results} == {ml_gpu_infer.GENERAL_CATEGORY}


@pytest.mark.unit
def test_select_predictions_applies_the_higher_of_tag_and_floor_threshold():
    results = ml_gpu_infer.select_predictions(
        np.array([0.5, 0.5], dtype=np.float32),
        ["a", "b"],
        [0, 0],
        [0.6, 0.2],  # a's own threshold is above 0.5; b's is below the 0.35 floor
        0.35,
        "m",
        {0},
    )
    assert [r["external_tag"] for r in results] == ["b"]


@pytest.mark.unit
def test_predict_wrapper_is_gone():
    """run() uses infer() + select_predictions(); no compatibility wrapper is kept."""
    assert not hasattr(ml_gpu_infer, "predict")


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


# ---------------------------------------------------------------------------
# Embedding store writer — the runner's append-only output, read back through
# the app's reader (app/services/ml_probe/store.py)
# ---------------------------------------------------------------------------


def _writer(tmp_path, meta=None):
    return ml_gpu_infer.EmbeddingWriter(tmp_path, dim=4, meta=meta or {"model": "m"})


@pytest.mark.unit
def test_writer_round_trips_through_reader(tmp_path):
    from app.services.ml_probe.store import open_embedding_store

    writer = ml_gpu_infer.EmbeddingWriter(
        tmp_path, dim=4, meta={"model": "m", "variant": "thumbs", "storage_root": "/x"}
    )
    writer.append(5, np.arange(4, dtype=np.float32))
    writer.append(9, np.ones(4, dtype=np.float32))
    writer.close()

    store = open_embedding_store(tmp_path)

    assert store.ids.tolist() == [5, 9]
    assert np.allclose(store.vectors[0], [0, 1, 2, 3])
    assert np.allclose(store.vectors[1], [1, 1, 1, 1])
    assert store.meta["model"] == "m"
    assert store.meta["dim"] == 4
    assert store.meta["dtype"] == "float16"
    assert store.meta["variant"] == "thumbs"
    assert store.meta["storage_root"] == "/x"
    assert "created_at" in store.meta


@pytest.mark.unit
def test_writer_resume_truncates_partial_vector(tmp_path):
    writer = _writer(tmp_path)
    for image_id in (1, 2, 3):
        writer.append(image_id, np.full(4, image_id, dtype=np.float32))
    writer.close()
    with open(tmp_path / "embeddings.f16", "ab") as f:
        f.write(b"\x00" * 8)  # crash mid-record: a half-written 4th vector, no id

    resumed = _writer(tmp_path)

    assert resumed.done_ids == {1, 2, 3}
    assert (tmp_path / "embeddings.f16").stat().st_size == 3 * 4 * 2
    assert (tmp_path / "embeddings.ids").stat().st_size == 3 * 8


@pytest.mark.unit
def test_writer_resume_truncates_orphan_id(tmp_path):
    writer = _writer(tmp_path)
    for image_id in (1, 2, 3):
        writer.append(image_id, np.full(4, image_id, dtype=np.float32))
    writer.close()
    with open(tmp_path / "embeddings.ids", "ab") as f:
        f.write(np.int64(4).tobytes())  # an id with no vector behind it

    resumed = _writer(tmp_path)

    assert resumed.done_ids == {1, 2, 3}
    assert (tmp_path / "embeddings.ids").stat().st_size == 3 * 8


@pytest.mark.unit
def test_writer_append_after_resume_continues_the_store(tmp_path):
    from app.services.ml_probe.store import open_embedding_store

    writer = _writer(tmp_path)
    writer.append(1, np.zeros(4, dtype=np.float32))
    writer.close()

    resumed = _writer(tmp_path)
    resumed.append(2, np.ones(4, dtype=np.float32))
    resumed.close()

    assert open_embedding_store(tmp_path).ids.tolist() == [1, 2]


@pytest.mark.unit
def test_writer_rejects_store_from_other_model(tmp_path):
    _writer(tmp_path, meta={"model": "m"}).close()

    with pytest.raises(ValueError, match="model"):
        _writer(tmp_path, meta={"model": "other"})


@pytest.mark.unit
def test_writer_rejects_store_with_other_dim(tmp_path):
    _writer(tmp_path, meta={"model": "m"}).close()

    with pytest.raises(ValueError, match="dim"):
        ml_gpu_infer.EmbeddingWriter(tmp_path, dim=8, meta={"model": "m"})


# ---------------------------------------------------------------------------
# Embedding extraction: prefetch, CLI validation, and the predict() split
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bounded_prefetch_preserves_order_and_yields_exceptions():
    def fn(i):
        if i == 2:
            raise ValueError("bad")
        return i * 10

    out = list(ml_gpu_infer.bounded_prefetch(fn, [0, 1, 2, 3], workers=2, window=2))

    assert [item for item, _ in out] == [0, 1, 2, 3]
    assert [value for _, value in out][:2] == [0, 10]
    assert isinstance(out[2][1], ValueError)
    assert out[3][1] == 30


@pytest.mark.unit
def test_bounded_prefetch_handles_empty_input():
    assert list(ml_gpu_infer.bounded_prefetch(lambda i: i, [], workers=2, window=2)) == []


@pytest.mark.unit
def test_cli_requires_out_or_embeddings_out():
    result = subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--manifest",
            "m",
            "--model-dir",
            "d",
            "--storage-path",
            "s",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "one of --out or --embeddings-out is required" in result.stderr


@pytest.mark.unit
def test_infer_returns_embedding_only_when_asked():
    class _TwoOutputSession:
        def run(self, outputs, feed):
            assert feed["input"].shape == (1, 3, 448, 448)
            table = {
                "prediction": np.array([[0.1, 0.9]], dtype=np.float32),
                "embedding": np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
            }
            return [table[name] for name in outputs]

    x = np.zeros((1, 3, 448, 448), dtype=np.float32)
    probs, emb = ml_gpu_infer.infer(_TwoOutputSession(), "input", x, with_embedding=False)
    assert probs.tolist() == pytest.approx([0.1, 0.9]) and emb is None
    probs, emb = ml_gpu_infer.infer(_TwoOutputSession(), "input", x, with_embedding=True)
    assert emb is not None and emb.tolist() == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# --embeddings-out end to end, against the real model (skipped without it)
# ---------------------------------------------------------------------------

_NEEDS_MODEL = pytest.mark.skipif(
    not (_MODEL_DIR / "model.onnx").exists(),
    reason="animetimm model not present; set ML_MODELS_PATH to the dir containing it",
)


def _synthetic_storage(tmp_path, image_ids, *, corrupt=()):
    """A fullsize/ dir with one random PNG per id (or junk bytes for ids in corrupt)."""
    storage = tmp_path / "storage"
    (storage / "fullsize").mkdir(parents=True, exist_ok=True)
    rows = []
    for image_id in image_ids:
        path = storage / "fullsize" / f"img{image_id}.png"
        if image_id in corrupt:
            path.write_bytes(b"not an image")
        else:
            arr = np.random.default_rng(image_id).integers(0, 256, (400, 300, 3), dtype=np.uint8)
            Image.fromarray(arr).save(path)
        rows.append({"image_id": image_id, "filename": f"img{image_id}", "ext": "png"})
    return storage, rows


def _run_embeddings(tmp_path, storage, rows, store_dir, name="m.jsonl"):
    manifest = tmp_path / name
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--manifest",
            str(manifest),
            "--embeddings-out",
            str(store_dir),
            "--model-dir",
            str(_MODEL_DIR),
            "--storage-path",
            str(storage),
            "--variant",
            "fullsize",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@_NEEDS_MODEL
async def test_embeddings_out_matches_canonical_embedding(tmp_path):
    from app.services.animetimm_model import AnimetimmModel
    from app.services.ml_probe.store import open_embedding_store

    storage, rows = _synthetic_storage(tmp_path, [123])
    store_dir = tmp_path / "emb"

    _run_embeddings(tmp_path, storage, rows, store_dir)

    store = open_embedding_store(store_dir)
    model = AnimetimmModel(str(_MODEL_DIR / "model.onnx"), str(_MODEL_DIR / "selected_tags.csv"))
    await model.load()
    try:
        canonical = await model.embed(str(storage / "fullsize" / "img123.png"))
    finally:
        await model.cleanup()

    assert store.ids.tolist() == [123]
    assert store.vectors.shape == (1, 1024)
    assert store.meta["dim"] == 1024
    assert store.meta["model"] == _MODEL_DIR.name
    assert canonical.shape == (1024,)
    assert np.max(np.abs(store.vectors[0].astype(np.float32) - canonical)) < 2e-2
    assert not list(tmp_path.glob("*.jsonl.meta")), "no predictions JSONL without --out"


@_NEEDS_MODEL
def test_embeddings_out_skips_unreadable_image(tmp_path):
    from app.services.ml_probe.store import open_embedding_store

    storage, rows = _synthetic_storage(tmp_path, [1, 2], corrupt={1})
    store_dir = tmp_path / "emb"

    proc = _run_embeddings(tmp_path, storage, rows, store_dir)

    assert open_embedding_store(store_dir).ids.tolist() == [2]
    assert "skipping image 1" in proc.stdout
    assert "1 unreadable" in proc.stdout


@_NEEDS_MODEL
def test_embeddings_out_resumes(tmp_path):
    from app.services.ml_probe.store import open_embedding_store

    storage, rows = _synthetic_storage(tmp_path, [1, 2, 3])
    store_dir = tmp_path / "emb"

    _run_embeddings(tmp_path, storage, rows[:2], store_dir, name="first.jsonl")
    proc = _run_embeddings(tmp_path, storage, rows, store_dir, name="second.jsonl")

    assert open_embedding_store(store_dir).ids.tolist() == [1, 2, 3]
    assert "2 already done, 1 to process" in proc.stdout


@pytest.mark.unit
def test_cli_rejects_shards_with_embeddings_out():
    """Two shard processes appending to one store would interleave rows and ids undetectably."""
    result = subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--manifest",
            "m",
            "--model-dir",
            "d",
            "--storage-path",
            "s",
            "--embeddings-out",
            "e",
            "--shards",
            "2",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--embeddings-out" in result.stderr and "--shards" in result.stderr


@pytest.mark.unit
def test_writer_refuses_a_second_writer_on_the_same_store(tmp_path):
    first = _writer(tmp_path)
    with pytest.raises(RuntimeError, match="another process"):
        _writer(tmp_path)
    first.close()
    second = _writer(tmp_path)  # released on close
    second.close()


@_NEEDS_MODEL
def test_embeddings_out_with_out_fills_each_output_independently(tmp_path):
    """Store and JSONL may be out of step; each gets exactly the images it is missing."""
    from app.services.ml_probe.store import open_embedding_store

    storage, rows = _synthetic_storage(tmp_path, [1, 2, 3])
    store_dir = tmp_path / "emb"
    _run_embeddings(tmp_path, storage, rows[:2], store_dir, name="first.jsonl")  # store: 1, 2
    out = tmp_path / "r.jsonl"
    out.write_text(json.dumps({"image_id": 1, "predictions": []}) + "\n")  # JSONL: 1 only

    manifest = tmp_path / "m.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    proc = subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--manifest",
            str(manifest),
            "--embeddings-out",
            str(store_dir),
            "--out",
            str(out),
            "--model-dir",
            str(_MODEL_DIR),
            "--storage-path",
            str(storage),
            "--variant",
            "fullsize",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert open_embedding_store(store_dir).ids.tolist() == [1, 2, 3]
    jsonl_ids = [json.loads(line)["image_id"] for line in out.read_text().splitlines()]
    assert jsonl_ids == [1, 2, 3], "image 2 repaired, image 1 not duplicated"
    assert "1 already done, 2 to process" in proc.stdout

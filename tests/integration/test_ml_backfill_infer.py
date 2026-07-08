"""Regression test for the Stage-2 offline backfill inference script.

scripts/ml_backfill_infer.py is a thin CLI script (not an importable package),
loaded here the same way tests/integration/test_ml_gpu_infer.py loads its
sibling ml_gpu_infer.py — via importlib against the file path.

This drives the real ``run()`` end-to-end against a fake tagging model (no
onnxruntime involved): ``MLTagSuggestionService.load_models`` is patched to
attach a protocol-compatible fake model directly, exactly as
tests/services/test_ml_service.py does, so ``generate_raw_predictions`` itself
still runs for real — only the ONNX-loading boundary is faked.

This is what caught the confirmed bug: the script called a
``generate_suggestions`` method that does not exist on MLTagSuggestionService
(only ``generate_raw_predictions`` does); the broad per-image
``except Exception`` swallowed the resulting AttributeError, so the run
"succeeded" with an empty output file.
"""

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.ml_service import MLTagSuggestionService

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "ml_backfill_infer.py"

_spec = importlib.util.spec_from_file_location("ml_backfill_infer", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ml_backfill_infer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml_backfill_infer)


class FakeTaggingModel:
    """Protocol-compatible fake mirroring tests/services/test_ml_service.py's fake.

    Records the args generate_raw_predictions forwards to the model, and
    returns canned predictions across two categories (general + character) so
    the test can confirm both are requested and passed through.
    """

    def __init__(self) -> None:
        self.last_include_categories: set[int] | None = None
        self.last_min_confidence: float | None = None

    async def predict(
        self,
        image_path: str,
        min_confidence: float = 0.35,
        include_categories: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        self.last_include_categories = include_categories
        self.last_min_confidence = min_confidence
        return [
            {"tag": "long_hair", "confidence": 0.9, "category": 0},
            {"tag": "hatsune_miku", "confidence": 0.8, "category": 4},
        ]

    async def cleanup(self) -> None:
        pass


@pytest.mark.unit
async def test_run_writes_raw_predictions_shaped_results(tmp_path, monkeypatch, capsys):
    """run() must call the real MLTagSuggestionService inference method and
    write JSONL rows already shaped for ml_backfill_ingest.py / ml_raw_ingest.py
    (external_tag/confidence/category/model_version per predicted tag)."""
    storage = tmp_path / "storage"
    (storage / "thumbs").mkdir(parents=True)
    (storage / "thumbs" / "img1.webp").write_bytes(b"not a real image; predict() is faked")

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"image_id": 1, "filename": "img1", "ext": "jpg"}) + "\n")
    out = tmp_path / "results.jsonl"

    fake = FakeTaggingModel()

    async def fake_load_models(self: MLTagSuggestionService) -> None:
        # Stand-in for the real ONNX load: attach the fake directly, the same
        # way tests/services/test_ml_service.py sets svc.model on a fresh
        # MLTagSuggestionService rather than loading a real model file.
        self.model = fake  # type: ignore[assignment]
        self._model_name = "caformer_b36.dbv4-full"

    monkeypatch.setattr(MLTagSuggestionService, "load_models", fake_load_models)
    monkeypatch.setattr(ml_backfill_infer.settings, "STORAGE_PATH", str(storage))
    monkeypatch.setattr(ml_backfill_infer.settings, "ML_MIN_CONFIDENCE", 0.42)

    args = argparse.Namespace(
        manifest=str(manifest), out=str(out), variant="thumbs", shards=1, shard_index=0
    )
    await ml_backfill_infer.run(args)

    captured = capsys.readouterr()
    # Pre-fix, the nonexistent generate_suggestions() call raises AttributeError,
    # which the per-image except swallows and logs — that must not happen.
    assert "AttributeError" not in captured.out
    assert "done: 1 processed, 0 missing-file, 0 unreadable" in captured.out

    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert rows == [
        {
            "image_id": 1,
            "predictions": [
                {
                    "external_tag": "long_hair",
                    "confidence": 0.9,
                    "category": 0,
                    "model_version": "caformer_b36.dbv4-full",
                },
                {
                    "external_tag": "hatsune_miku",
                    "confidence": 0.8,
                    "category": 4,
                    "model_version": "caformer_b36.dbv4-full",
                },
            ],
        }
    ]
    # Both general (0) and character (4) categories were requested — matching
    # SUGGESTION_CATEGORIES, the same set the live upload path uses — and the
    # configured confidence floor was forwarded, not a hardcoded default.
    assert fake.last_include_categories == {0, 4}
    assert fake.last_min_confidence == 0.42

#!/usr/bin/env python3
"""Standalone GPU-host inference runner for the ML tag-suggestion backfill.

This is the offline backfill's Stage 2 (see docs/ml-tag-suggestions.md) packaged
to run on a *separate* host with **no app/DB dependencies** — only onnxruntime,
numpy, and pillow. That lets it run in a Python 3.12 + onnxruntime-rocm venv on
an AMD GPU box (e.g. ROCm on a Radeon card) where the main app's Python 3.14 /
onnxruntime-CPU stack can't be installed.

It is a deliberate, self-contained twin of `ml_backfill_infer.py`: it reads the
same manifest, writes the same results JSONL that `ml_backfill_ingest.py`
consumes, and is sharded + resumable the same way. The preprocessing and
selection logic below are copied from `app/services/animetimm_model.py` and the
themes-only filter in `app/services/ml_service.py`; they MUST stay in sync.
`tests/integration/test_ml_gpu_infer.py` guards against drift by asserting this
runner's output matches the canonical AnimetimmModel on the real model.

Targets the animetimm swinv2 model (ImageNet preprocessing, "prediction"
output). v1 keeps general (theme) tags only.

Usage:
    python ml_gpu_infer.py --manifest m.jsonl --out results.jsonl \
        --model-dir ~/ml-models/swinv2_base_window8_256.dbv4-full \
        --storage-path /mnt/shuushuu --variant thumbs
"""

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from PIL import Image

GENERAL_CATEGORY = 0  # theme/general tags; v1 keeps only these


# --- preprocessing: app-free copy of app/services/animetimm_preprocess.py ---
# Driven by each model's preprocess.json so any animetimm model is a drop-in.
# tests/integration/test_ml_gpu_infer.py guards this copy against the canonical.

_FILTERS = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}

DEFAULT_TEST_PIPELINE: list[dict[str, Any]] = [
    {
        "type": "pad_to_size",
        "size": [512, 512],
        "background_color": "white",
        "interpolation": "bilinear",
    },
    {"type": "resize", "size": 448, "interpolation": "bicubic"},
    {"type": "center_crop", "size": [448, 448]},
    {"type": "maybe_to_tensor"},
    {"type": "normalize", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
]


def load_test_pipeline(model_dir: Path) -> list[dict[str, Any]]:
    path = model_dir / "preprocess.json"
    if not path.exists():
        return DEFAULT_TEST_PIPELINE
    pipeline: list[dict[str, Any]] = json.loads(path.read_text())["test"]
    return pipeline


def _filter(name: str) -> Image.Resampling:
    try:
        return _FILTERS[name]
    except KeyError:
        raise ValueError(f"unsupported interpolation: {name}") from None


def load_rgb(image_path: str) -> Image.Image:
    img = Image.open(image_path).convert("RGBA")
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    background.paste(img, mask=img.split()[3])
    return background.convert("RGB")


def apply_test_pipeline(
    img: Image.Image, pipeline: list[dict[str, Any]]
) -> np.ndarray[Any, np.dtype[np.float32]]:
    arr: np.ndarray[Any, np.dtype[np.float32]] | None = None
    for op in pipeline:
        kind = op["type"]
        if kind == "pad_to_size":
            target_w, target_h = op["size"]
            bg = (255, 255, 255) if op.get("background_color") == "white" else (0, 0, 0)
            scale = min(target_w / img.width, target_h / img.height)
            new = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new, _filter(op.get("interpolation", "bilinear")))
            canvas = Image.new("RGB", (target_w, target_h), bg)
            canvas.paste(img, ((target_w - new[0]) // 2, (target_h - new[1]) // 2))
            img = canvas
        elif kind == "resize":
            size = op["size"]
            if isinstance(size, int):
                scale = size / min(img.width, img.height)
                new = (int(img.width * scale), int(img.height * scale))
            else:
                new = (size[1], size[0])
            img = img.resize(new, _filter(op.get("interpolation", "bicubic")))
        elif kind == "center_crop":
            crop_h, crop_w = op["size"]
            left = (img.width - crop_w) // 2
            top = (img.height - crop_h) // 2
            img = img.crop((left, top, left + crop_w, top + crop_h))
        elif kind == "maybe_to_tensor":
            arr = np.asarray(img, dtype=np.float32) / 255.0
            arr = np.transpose(arr, (2, 0, 1))
        elif kind == "normalize":
            if arr is None:
                raise ValueError("normalize op before maybe_to_tensor in pipeline")
            mean = np.array(op["mean"], dtype=np.float32).reshape(3, 1, 1)
            std = np.array(op["std"], dtype=np.float32).reshape(3, 1, 1)
            arr = (arr - mean) / std
        else:
            raise ValueError(f"unsupported preprocess op: {kind}")
    if arr is None:
        raise ValueError("preprocess pipeline produced no tensor (missing maybe_to_tensor)")
    return np.expand_dims(arr, axis=0)


# --- provider selection (mirrors app/services/onnx_providers.py) ---


def select_providers() -> list[str]:
    available = ort.get_available_providers()
    preferred = ["CUDAExecutionProvider", "ROCMExecutionProvider", "CPUExecutionProvider"]
    selected = [p for p in preferred if p in available]
    if "CPUExecutionProvider" not in selected:
        selected.append("CPUExecutionProvider")
    return selected


# --- model + inference (copied from AnimetimmModel) ---


def load_model(
    model_dir: Path,
) -> tuple[ort.InferenceSession, str, list[str], list[int], list[float], list[dict[str, Any]]]:
    model_path = model_dir / "model.onnx"
    tags_path = model_dir / "selected_tags.csv"
    if not model_path.exists() or not tags_path.exists():
        raise FileNotFoundError(f"model.onnx and selected_tags.csv required in {model_dir}")

    session = ort.InferenceSession(str(model_path), providers=select_providers())
    input_name = session.get_inputs()[0].name

    names: list[str] = []
    categories: list[int] = []
    thresholds: list[float] = []
    with open(tags_path) as f:
        for row in csv.DictReader(f):
            names.append(row["name"])
            categories.append(int(row["category"]))
            thresholds.append(float(row.get("best_threshold", 0.35)))
    pipeline = load_test_pipeline(model_dir)
    return session, input_name, names, categories, thresholds, pipeline


def predict(
    session: ort.InferenceSession,
    input_name: str,
    names: list[str],
    categories: list[int],
    thresholds: list[float],
    pipeline: list[dict[str, Any]],
    image_path: str,
    min_confidence: float,
    model_version: str,
) -> list[dict[str, Any]]:
    """Theme-tag predictions for one image, shaped for ml_backfill_ingest."""
    img_array = apply_test_pipeline(load_rgb(image_path), pipeline)
    # "prediction" output already has sigmoid applied.
    probabilities = session.run(["prediction"], {input_name: img_array})[0][0]

    results: list[dict[str, Any]] = []
    for name, category, prob, tag_threshold in zip(
        names, categories, probabilities, thresholds, strict=True
    ):
        if category != GENERAL_CATEGORY:  # v1: theme tags only
            continue
        if prob < max(tag_threshold, min_confidence):
            continue
        results.append(
            {"external_tag": name, "confidence": float(prob), "model_version": model_version}
        )
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results


# --- JSONL / shard helpers (standalone copies of app/services/ml_backfill.py) ---


def variant_relpath(variant: str, filename: str, ext: str) -> str:
    suffix = "webp" if variant == "thumbs" else ext
    return f"{variant}/{filename}.{suffix}"


def select_shard(records: list[dict[str, Any]], shards: int, index: int) -> list[dict[str, Any]]:
    if shards < 1:
        raise ValueError("shards must be >= 1")
    if not 0 <= index < shards:
        raise ValueError(f"index must be in [0, {shards}); got {index}")
    return [r for i, r in enumerate(records) if i % shards == index]


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with open(path) as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"  warning: skipping malformed line {lineno} in {path}")


def load_image_ids(path: Path) -> set[int]:
    return {int(rec["image_id"]) for rec in iter_jsonl(path)}


def check_shard_output(out_path: Path, shards: int, shard_index: int) -> None:
    meta_path = out_path.with_name(out_path.name + ".meta")
    identity = {"shards": shards, "shard_index": shard_index}
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        if existing != identity:
            raise ValueError(
                f"{out_path} belongs to {existing}, not {identity}; "
                "use a dedicated output file per shard"
            )
    else:
        meta_path.write_text(json.dumps(identity))


def run(args: argparse.Namespace) -> None:
    model_dir = Path(args.model_dir)
    model_version = args.model_name or model_dir.name
    out = Path(args.out)
    check_shard_output(out, args.shards, args.shard_index)

    manifest = list(iter_jsonl(Path(args.manifest)))
    shard = select_shard(manifest, args.shards, args.shard_index)
    done = load_image_ids(out)
    todo = [rec for rec in shard if rec["image_id"] not in done]
    print(
        f"shard {args.shard_index}/{args.shards}: {len(shard)} images, "
        f"{len(done)} already done, {len(todo)} to process"
    )

    session, input_name, names, categories, thresholds, pipeline = load_model(model_dir)
    print(f"providers: {session.get_providers()}")

    storage = Path(args.storage_path)
    processed = 0
    missing = 0
    failed = 0
    with open(out, "a") as out_fh:
        for rec in todo:
            path = storage / variant_relpath(args.variant, rec["filename"], rec["ext"])
            if not path.exists():
                fallback = storage / variant_relpath("fullsize", rec["filename"], rec["ext"])
                if not fallback.exists():
                    missing += 1
                    continue
                path = fallback

            # A corrupt/unreadable image (or any per-image inference error) must
            # not abort a million-image run — log it and move on.
            try:
                predictions = predict(
                    session, input_name, names, categories, thresholds, pipeline,
                    str(path), args.min_confidence, model_version,
                )
            except Exception as exc:
                failed += 1
                print(f"  warning: skipping image {rec['image_id']} ({path}): "
                      f"{type(exc).__name__}: {exc}")
                continue
            out_fh.write(json.dumps({"image_id": rec["image_id"], "predictions": predictions}) + "\n")
            out_fh.flush()
            processed += 1
            if processed % 500 == 0:
                print(f"  {processed}/{len(todo)} processed...")

    print(f"done: {processed} processed, {missing} missing-file, {failed} unreadable → {out}")

    # onnxruntime's ROCm runtime can corrupt the heap in its teardown
    # destructors at interpreter exit ("corrupted size vs prev_size in
    # fastbins"). All results are written and flushed above (the output file is
    # closed by the `with` block), so hard-exit now to skip the buggy teardown
    # and the session destructor that triggers it. Exit 0 keeps shard
    # orchestration happy.
    sys.stdout.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone GPU-host ML tag-suggestion inference (animetimm swinv2)."
    )
    parser.add_argument("--manifest", required=True, help="Manifest JSONL from ml_backfill_manifest.py")
    parser.add_argument("--out", required=True, help="Output results JSONL (appended; resumable)")
    parser.add_argument("--model-dir", required=True, help="Dir with model.onnx + selected_tags.csv")
    parser.add_argument("--storage-path", required=True, help="Image store root (holds thumbs/, fullsize/, ...)")
    parser.add_argument("--model-name", default=None, help="model_version label (default: model dir name)")
    parser.add_argument("--variant", default="thumbs", choices=["thumbs", "medium", "large", "fullsize"])
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    run(parser.parse_args())


if __name__ == "__main__":
    main()

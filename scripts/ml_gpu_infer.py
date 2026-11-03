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
category-selection logic in `app/services/ml_service.py`; they MUST stay in sync.
`tests/integration/test_ml_gpu_infer.py` guards against drift by asserting this
runner's output matches the canonical AnimetimmModel on the real model.

Targets animetimm models (preprocessing is driven by each model's
preprocess.json — see below). By default emits general (theme) and character
tags; pass --no-include-character to restrict to general only.

Usage:
    python ml_gpu_infer.py --manifest m.jsonl --out results.jsonl \
        --model-dir ~/ml-models/swinv2_base_window8_256.dbv4-full \
        --storage-path /mnt/shuushuu --variant thumbs
"""

import argparse
import csv
import itertools
import json
import os
import sys
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from numpy.typing import NDArray
from PIL import Image

GENERAL_CATEGORY = 0  # theme/general tags
CHARACTER_CATEGORY = 4  # character tags


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
            target_h, target_w = op["size"]  # [h, w] (timm convention, as resize/center_crop)
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


def infer(
    session: ort.InferenceSession,
    input_name: str,
    img_array: NDArray[np.float32],
    *,
    with_embedding: bool,
) -> tuple[NDArray[np.float32], NDArray[np.float32] | None]:
    """One forward pass for one preprocessed image.

    "prediction" already has sigmoid applied. "embedding" is the pooled 1024-d
    feature vector the Danbooru head consumes; requesting it from the same run
    costs nothing extra.
    """
    outputs = ["prediction", "embedding"] if with_embedding else ["prediction"]
    results = session.run(outputs, {input_name: img_array})
    probabilities = results[0][0]
    embedding = results[1][0] if with_embedding else None
    return probabilities, embedding


def select_predictions(
    probabilities: NDArray[np.float32],
    names: list[str],
    categories: list[int],
    thresholds: list[float],
    min_confidence: float,
    model_version: str,
    allowed_categories: set[int],
) -> list[dict[str, Any]]:
    """Threshold and shape per-tag probabilities for ml_backfill_ingest."""
    results: list[dict[str, Any]] = []
    for name, category, prob, tag_threshold in zip(
        names, categories, probabilities, thresholds, strict=True
    ):
        if category not in allowed_categories:
            continue
        if prob < max(tag_threshold, min_confidence):
            continue
        results.append(
            {
                "external_tag": name,
                "confidence": float(prob),
                "model_version": model_version,
                "category": category,
            }
        )
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results


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
    allowed_categories: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Tag predictions for one image, shaped for ml_backfill_ingest.

    allowed_categories controls which tag categories are emitted.  Defaults to
    {GENERAL_CATEGORY} when not supplied (general/theme tags only).
    """
    if allowed_categories is None:
        allowed_categories = {GENERAL_CATEGORY}

    img_array = apply_test_pipeline(load_rgb(image_path), pipeline)
    probabilities, _ = infer(session, input_name, img_array, with_embedding=False)
    return select_predictions(
        probabilities,
        names,
        categories,
        thresholds,
        min_confidence,
        model_version,
        allowed_categories,
    )


def bounded_prefetch[T, R](
    fn: Callable[[T], R], items: Iterable[T], *, workers: int, window: int
) -> Iterator[tuple[T, R | Exception]]:
    """Run ``fn`` over ``items`` on a thread pool, yielding in input order.

    At most ``window`` results are in flight, so a million-row manifest is never
    submitted at once. An exception raised by ``fn`` is yielded as that item's
    value rather than raised, so one bad image cannot end the run.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    iterator = iter(items)
    pending: deque[tuple[T, Future[R]]] = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item in itertools.islice(iterator, window):
            pending.append((item, pool.submit(fn, item)))
        while pending:
            item, future = pending.popleft()
            value: R | Exception
            try:
                value = future.result()
            except Exception as exc:
                value = exc
            for nxt in itertools.islice(iterator, 1):
                pending.append((nxt, pool.submit(fn, nxt)))
            yield item, value


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


# --- embedding store writer (reader: app/services/ml_probe/store.py) ---

EMBEDDINGS_VECTORS_FILE = "embeddings.f16"
EMBEDDINGS_IDS_FILE = "embeddings.ids"
EMBEDDINGS_META_FILE = "embeddings.meta.json"
_VECTOR_ITEMSIZE = np.dtype(np.float16).itemsize
_ID_ITEMSIZE = np.dtype(np.int64).itemsize


class EmbeddingWriter:
    """Append-only float16 embedding store, resumable after a crash.

    Each record is the vector, flushed, then its int64 id, flushed, so an
    interrupted run can leave at most a trailing partial record. On open,
    both files are truncated to their longest consistent prefix and the ids
    already present become ``done_ids``. The meta sidecar pins the model and
    dimension so two runs can't interleave different embeddings in one store.
    """

    def __init__(self, directory: Path, dim: int, meta: dict[str, Any]) -> None:
        self.directory = Path(directory)
        self.dim = dim
        self.directory.mkdir(parents=True, exist_ok=True)
        self._reconcile_meta(meta)
        self._truncate_to_consistent_rows()
        ids_path = self.directory / EMBEDDINGS_IDS_FILE
        self.done_ids: set[int] = {int(i) for i in np.fromfile(ids_path, dtype="<i8")}
        # Handles stay open for the life of the writer; close() releases them.
        self._vectors = open(self.directory / EMBEDDINGS_VECTORS_FILE, "ab")  # noqa: SIM115
        self._ids = open(ids_path, "ab")  # noqa: SIM115

    def _reconcile_meta(self, meta: dict[str, Any]) -> None:
        meta_path = self.directory / EMBEDDINGS_META_FILE
        if meta_path.exists():
            existing = json.loads(meta_path.read_text())
            if existing.get("model") != meta.get("model"):
                raise ValueError(
                    f"{self.directory} holds embeddings for model {existing.get('model')!r}, "
                    f"not {meta.get('model')!r}"
                )
            if int(existing.get("dim", -1)) != self.dim:
                raise ValueError(
                    f"{self.directory} holds dim={existing.get('dim')} embeddings, not dim={self.dim}"
                )
            return
        full = {
            **meta,
            "dim": self.dim,
            "dtype": "float16",
            "created_at": datetime.now(UTC).isoformat(),
        }
        meta_path.write_text(json.dumps(full, indent=1) + "\n")

    def _truncate_to_consistent_rows(self) -> None:
        vectors_path = self.directory / EMBEDDINGS_VECTORS_FILE
        ids_path = self.directory / EMBEDDINGS_IDS_FILE
        vectors_path.touch()
        ids_path.touch()
        rows = min(
            vectors_path.stat().st_size // (self.dim * _VECTOR_ITEMSIZE),
            ids_path.stat().st_size // _ID_ITEMSIZE,
        )
        os.truncate(vectors_path, rows * self.dim * _VECTOR_ITEMSIZE)
        os.truncate(ids_path, rows * _ID_ITEMSIZE)

    def append(self, image_id: int, vector: NDArray[np.floating]) -> None:
        arr = np.ascontiguousarray(np.asarray(vector).reshape(-1).astype("<f2"))
        if arr.shape[0] != self.dim:
            raise ValueError(f"vector has {arr.shape[0]} values, store dim is {self.dim}")
        self._vectors.write(arr.tobytes())
        self._vectors.flush()
        self._ids.write(np.array(image_id, dtype="<i8").tobytes())
        self._ids.flush()
        self.done_ids.add(int(image_id))

    def close(self) -> None:
        self._vectors.close()
        self._ids.close()


def _resolve_image_path(storage: Path, variant: str, rec: dict[str, Any]) -> Path | None:
    path = storage / variant_relpath(variant, rec["filename"], rec["ext"])
    if path.exists():
        return path
    fallback = storage / variant_relpath("fullsize", rec["filename"], rec["ext"])
    return fallback if fallback.exists() else None


def _embedding_dim(session: ort.InferenceSession) -> int:
    for output in session.get_outputs():
        if output.name == "embedding":
            return int(output.shape[1])
    raise ValueError("model has no 'embedding' output")


@dataclass
class _ShardJob:
    """Everything the per-image loop needs, bundled so the loop stays one function."""

    session: ort.InferenceSession
    input_name: str
    names: list[str]
    categories: list[int]
    thresholds: list[float]
    pipeline: list[dict[str, Any]]
    storage: Path
    variant: str
    min_confidence: float
    model_version: str
    allowed_categories: set[int]
    prefetch_workers: int
    writer: EmbeddingWriter | None
    out_fh: TextIO | None


def _process_shard(job: _ShardJob, todo: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Infer every record in ``todo``; returns (processed, missing, failed)."""

    def load_tensor(rec: dict[str, Any]) -> NDArray[np.float32] | None:
        path = _resolve_image_path(job.storage, job.variant, rec)
        if path is None:
            return None
        return apply_test_pipeline(load_rgb(str(path)), job.pipeline)

    processed = missing = failed = 0
    prefetched = bounded_prefetch(
        load_tensor, todo, workers=job.prefetch_workers, window=4 * job.prefetch_workers
    )
    for rec, tensor in prefetched:
        if tensor is None:
            missing += 1
            continue
        # A corrupt/unreadable image (or any per-image inference error) must
        # not abort a million-image run — log it and move on.
        try:
            if isinstance(tensor, Exception):
                raise tensor
            probabilities, embedding = infer(
                job.session, job.input_name, tensor, with_embedding=job.writer is not None
            )
        except Exception as exc:
            failed += 1
            print(
                f"  warning: skipping image {rec['image_id']} "
                f"({rec['filename']}.{rec['ext']}): {type(exc).__name__}: {exc}"
            )
            continue
        if job.writer is not None and embedding is not None:
            job.writer.append(rec["image_id"], embedding)
        if job.out_fh is not None:
            predictions = select_predictions(
                probabilities,
                job.names,
                job.categories,
                job.thresholds,
                job.min_confidence,
                job.model_version,
                job.allowed_categories,
            )
            job.out_fh.write(
                json.dumps({"image_id": rec["image_id"], "predictions": predictions}) + "\n"
            )
            job.out_fh.flush()
        processed += 1
        if processed % 500 == 0:
            print(f"  {processed}/{len(todo)} processed...")
    return processed, missing, failed


def run(args: argparse.Namespace) -> None:
    model_dir = Path(args.model_dir)
    model_version = args.model_name or model_dir.name
    out = Path(args.out) if args.out else None
    if out is not None:
        check_shard_output(out, args.shards, args.shard_index)

    manifest = list(iter_jsonl(Path(args.manifest)))
    shard = select_shard(manifest, args.shards, args.shard_index)

    session, input_name, names, categories, thresholds, pipeline = load_model(model_dir)
    print(f"providers: {session.get_providers()}")

    writer: EmbeddingWriter | None = None
    if args.embeddings_out:
        writer = EmbeddingWriter(
            Path(args.embeddings_out),
            _embedding_dim(session),
            {
                "model": model_version,
                "variant": args.variant,
                "storage_root": str(args.storage_path),
            },
        )
        done = set(writer.done_ids)
    else:
        assert out is not None  # main() guarantees one of --out / --embeddings-out
        done = load_image_ids(out)
    todo = [rec for rec in shard if rec["image_id"] not in done]
    print(
        f"shard {args.shard_index}/{args.shards}: {len(shard)} images, "
        f"{len(done)} already done, {len(todo)} to process"
    )

    allowed_categories: set[int] = {GENERAL_CATEGORY}
    if args.include_character:
        allowed_categories.add(CHARACTER_CATEGORY)

    out_fh = open(out, "a") if out is not None else None  # noqa: SIM115 - closed in finally
    try:
        job = _ShardJob(
            session=session,
            input_name=input_name,
            names=names,
            categories=categories,
            thresholds=thresholds,
            pipeline=pipeline,
            storage=Path(args.storage_path),
            variant=args.variant,
            min_confidence=args.min_confidence,
            model_version=model_version,
            allowed_categories=allowed_categories,
            prefetch_workers=args.prefetch_workers,
            writer=writer,
            out_fh=out_fh,
        )
        processed, missing, failed = _process_shard(job, todo)
    finally:
        if out_fh is not None:
            out_fh.close()
        if writer is not None:
            writer.close()

    targets = " + ".join(str(t) for t in (out, args.embeddings_out) if t)
    print(f"done: {processed} processed, {missing} missing-file, {failed} unreadable → {targets}")

    # onnxruntime's ROCm runtime can corrupt the heap in its teardown
    # destructors at interpreter exit ("corrupted size vs prev_size in
    # fastbins"). All results are written and flushed above (the output files are
    # closed in the `finally`), so hard-exit now to skip the buggy teardown
    # and the session destructor that triggers it. Exit 0 keeps shard
    # orchestration happy.
    sys.stdout.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone GPU-host ML tag-suggestion inference (animetimm swinv2)."
    )
    parser.add_argument(
        "--manifest", required=True, help="Manifest JSONL from ml_backfill_manifest.py"
    )
    parser.add_argument("--out", default=None, help="Output results JSONL (appended; resumable)")
    parser.add_argument(
        "--embeddings-out",
        default=None,
        help="Directory for the embedding store (embeddings.f16 + .ids + .meta.json; "
        "appended; resumable). With this set, --out is optional.",
    )
    parser.add_argument(
        "--model-dir", required=True, help="Dir with model.onnx + selected_tags.csv"
    )
    parser.add_argument(
        "--storage-path", required=True, help="Image store root (holds thumbs/, fullsize/, ...)"
    )
    parser.add_argument(
        "--model-name", default=None, help="model_version label (default: model dir name)"
    )
    parser.add_argument(
        "--variant", default="thumbs", choices=["thumbs", "medium", "large", "fullsize"]
    )
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument(
        "--prefetch-workers",
        type=int,
        default=4,
        help="Threads decoding and preprocessing images ahead of the GPU (default: 4).",
    )
    parser.add_argument(
        "--include-character",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Emit character tags (category 4) in addition to general tags (default: on).",
    )
    args = parser.parse_args()
    if not args.out and not args.embeddings_out:
        parser.error("one of --out or --embeddings-out is required")
    run(args)


if __name__ == "__main__":
    main()

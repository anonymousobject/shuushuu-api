#!/usr/bin/env python3
"""Run ML tag-suggestion inference over a manifest, writing external-tag predictions.

Stage 2 of the offline backfill (see docs/ml-tag-suggestions.md). Runs wherever
onnxruntime can load the model — CPU, CUDA, or ROCm are auto-detected, so the
same command uses a GPU on a host that has one. Reads image files from
STORAGE_PATH (thumbnails by default; they downscale to the model's 448px input
with no quality loss for theme tags) and appends JSONL results that
ml_backfill_ingest.py feeds into the database.

Sharded and resumable: run one shard per host/process, and re-running skips
images already present in the output file.

Usage:
    uv run python scripts/ml_backfill_infer.py --manifest m.jsonl --out results.jsonl
    # split across two hosts:
    uv run python scripts/ml_backfill_infer.py --manifest m.jsonl --out r0.jsonl --shards 2 --shard-index 0
    uv run python scripts/ml_backfill_infer.py --manifest m.jsonl --out r1.jsonl --shards 2 --shard-index 1
"""

import argparse
import asyncio
from pathlib import Path

from app.config import settings
from app.services.ml_backfill import (
    check_shard_output,
    iter_results,
    load_image_ids,
    select_shard,
    variant_relpath,
    write_results,
)


async def run(args: argparse.Namespace) -> None:
    # Imported locally so onnxruntime is loaded only when actually running
    # inference, never just by importing the backfill helpers.
    from app.services.ml_service import MLTagSuggestionService

    manifest = list(iter_results(Path(args.manifest)))
    shard = select_shard(manifest, args.shards, args.shard_index)
    out = Path(args.out)
    # Guard against resuming the wrong shard into this file (would skip the wrong
    # images and interleave results).
    check_shard_output(out, args.shards, args.shard_index)
    done = load_image_ids(out)  # resume: skip images already written
    todo = [rec for rec in shard if rec["image_id"] not in done]
    print(
        f"shard {args.shard_index}/{args.shards}: {len(shard)} images, "
        f"{len(done)} already done, {len(todo)} to process"
    )

    storage = Path(settings.STORAGE_PATH)
    service = MLTagSuggestionService()
    await service.load_models()

    processed = 0
    missing = 0
    failed = 0
    try:
        for rec in todo:
            path = storage / variant_relpath(args.variant, rec["filename"], rec["ext"])
            if not path.exists():
                # Fall back to fullsize when the preferred variant is absent.
                fallback = storage / variant_relpath("fullsize", rec["filename"], rec["ext"])
                if not fallback.exists():
                    missing += 1
                    continue
                path = fallback

            # A corrupt/unreadable image must not abort the whole run.
            try:
                predictions = await service.generate_suggestions(
                    str(path), min_confidence=settings.ML_MIN_CONFIDENCE
                )
            except Exception as exc:
                failed += 1
                print(f"  warning: skipping image {rec['image_id']} ({path}): "
                      f"{type(exc).__name__}: {exc}")
                continue
            write_results(out, [{"image_id": rec["image_id"], "predictions": predictions}])
            processed += 1
            if processed % 500 == 0:
                print(f"  {processed}/{len(todo)} processed...")
    finally:
        await service.cleanup()

    print(f"done: {processed} processed, {missing} missing-file, {failed} unreadable → {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run ML tag-suggestion inference over a manifest (CPU/CUDA/ROCm)."
    )
    parser.add_argument("--manifest", required=True, help="Manifest JSONL from ml_backfill_manifest.py")
    parser.add_argument("--out", required=True, help="Output results JSONL (appended; resumable)")
    parser.add_argument(
        "--variant",
        default="thumbs",
        choices=["thumbs", "medium", "large", "fullsize"],
        help="Image variant to feed the model (default: thumbs)",
    )
    parser.add_argument("--shards", type=int, default=1, help="Total number of shards")
    parser.add_argument("--shard-index", type=int, default=0, help="Which shard this run handles")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

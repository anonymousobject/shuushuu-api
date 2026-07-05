#!/usr/bin/env python3
"""Re-map raw ML predictions into tag suggestions using current tag_mappings.

Reads image IDs from ml_raw_predictions for the given model and calls
remap_image_from_store for each one. The result is a fresh set of pending
MlTagSuggestions rows derived from the current mapping/resolution state —
without re-running GPU inference.

Resumable via a checkpoint file that records already-processed image IDs.
A row that fails (e.g. the image was deleted) is logged and skipped.

Usage:
    uv run python scripts/ml_remap.py --model caformer_b36.dbv4-full
    uv run python scripts/ml_remap.py --model caformer_b36.dbv4-full --image-id 12345
    uv run python scripts/ml_remap.py --model caformer_b36.dbv4-full --limit 500 --checkpoint remap.done
"""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.core.database import get_async_session
from app.models.ml_raw_prediction import MlModels, MlRawPredictions
from app.services.ml_backfill import load_image_ids, write_results
from app.services.ml_remap import remap_image_from_store, remap_images_for_tag


async def run(args: argparse.Namespace) -> None:
    model_name: str = args.model
    single_tag_id: int | None = args.tag

    print(f"model: {model_name}")

    async with get_async_session() as db:
        if single_tag_id is not None:
            print(f"tag: {single_tag_id} (scoped remap)")
            count = await remap_images_for_tag(db, single_tag_id, model_name)
            print(f"done: images_remapped={count}")
            return

        checkpoint = Path(args.checkpoint)
        already_done = load_image_ids(checkpoint)
        limit: int | None = args.limit
        single_image_id: int | None = args.image_id

        print(f"checkpoint: {checkpoint} ({len(already_done)} already processed)")

        processed = 0
        added_total = 0
        skipped = 0
        errors: list[str] = []

        if single_image_id is not None:
            image_ids = [single_image_id]
        else:
            # Fetch distinct image IDs for this model, ordered for resumable checkpointing.
            stmt = (
                select(MlRawPredictions.image_id)
                .join(MlModels, MlModels.id == MlRawPredictions.model_id)
                .where(MlModels.name == model_name)
                .distinct()
                .order_by(MlRawPredictions.image_id)
            )
            rows = (await db.execute(stmt)).all()
            image_ids = [row[0] for row in rows]

        print(f"image IDs to process: {len(image_ids)}")

        for image_id in image_ids:
            if image_id in already_done:
                skipped += 1
                continue
            if limit is not None and processed >= limit:
                break

            try:
                added = await remap_image_from_store(db, image_id, model_name)
                added_total += added
                processed += 1
                write_results(checkpoint, [{"image_id": image_id}])
                if processed % 100 == 0:
                    print(f"  processed={processed} added_total={added_total}")
            except Exception as exc:
                # Roll back so a failed commit doesn't poison the session and
                # cascade PendingRollbackError onto every later image (mirrors
                # ml_backfill.ingest_results' skip-and-continue contract).
                await db.rollback()
                errors.append(f"image_id={image_id}: {exc}")
                print(f"  error image_id={image_id}: {exc}")

    print(
        f"done: processed={processed} added_total={added_total} skipped={skipped} errors={len(errors)}"
    )
    for err in errors[:20]:
        print(f"  error: {err}")
    if len(errors) > 20:
        print(f"  ... and {len(errors) - 20} more")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-map raw ML predictions into tag suggestions via current tag_mappings."
    )
    parser.add_argument(
        "--model",
        required=True,
        help="ML model name to re-map (e.g. caformer_b36.dbv4-full)",
    )
    parser.add_argument(
        "--tag",
        type=int,
        default=None,
        metavar="INTERNAL_TAG_ID",
        help=(
            "Re-map only images that have raw predictions for external tags mapping "
            "to this internal tag ID (fast onboarding path; skips checkpoint/limit)"
        ),
    )
    parser.add_argument(
        "--image-id",
        type=int,
        default=None,
        metavar="ID",
        help="Re-map a single image ID only (default: all images for the model)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Stop after processing N images (default: no limit)",
    )
    parser.add_argument(
        "--checkpoint",
        default="ml_remap.done",
        help="JSONL checkpoint file of processed image IDs (default: ml_remap.done)",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

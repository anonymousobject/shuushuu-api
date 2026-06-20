"""Helpers for the offline ML tag-suggestion backfill.

Splits the bulk job into three stages so GPU inference can run on a separate
host without touching the database:

1. manifest  — query the DB for (image_id, filename, ext) rows to process
2. inference — (on the GPU host) read images, write external-tag predictions
3. ingest    — feed those predictions through store_predictions next to the DB

This module holds the manifest query, the pure path/shard/JSONL helpers, and
the resilient ingest loop. It deliberately does NOT import the ML service, so
importing it never pulls onnxruntime into the API/DB process.
"""

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.logging import get_logger
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.services.ml_suggestion_pipeline import store_predictions

logger = get_logger(__name__)


def variant_relpath(variant: str, filename: str, ext: str) -> str:
    """Storage-relative path for an image variant.

    Thumbnails are always WebP; every other variant keeps the original ext.
    Matches the layout the upload pipeline writes ({variant}/{filename}.{ext}).
    """
    suffix = "webp" if variant == "thumbs" else ext
    return f"{variant}/{filename}.{suffix}"


def select_shard[T](records: list[T], shards: int, index: int) -> list[T]:
    """Return the ``index``-th of ``shards`` disjoint, round-robin slices.

    Splitting by position keeps each shard roughly equal regardless of how
    image IDs are distributed, so N hosts/processes can each take one shard.
    """
    if shards < 1:
        raise ValueError("shards must be >= 1")
    if not 0 <= index < shards:
        raise ValueError(f"index must be in [0, {shards}); got {index}")
    return [record for i, record in enumerate(records) if i % shards == index]


def write_results(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Append result rows to a JSONL file (one JSON object per line)."""
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def iter_results(path: Path) -> Iterator[dict[str, Any]]:
    """Yield result objects from a JSONL file; nothing if it doesn't exist.

    A malformed line (e.g. a truncated trailing line left by a hard kill
    mid-write) is logged and skipped rather than aborting the whole read, so
    resume survives an unclean shutdown of a previous run.
    """
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
                logger.warning("ml_backfill_skipping_bad_line", path=str(path), line=lineno)


def check_shard_output(out_path: Path, shards: int, shard_index: int) -> None:
    """Bind an output file to its (shards, shard_index) to make resume safe.

    Resume skips images already present in the output file, so pointing the
    wrong shard at an existing file would silently skip the wrong images and
    interleave results. On first use this records the shard identity in a
    ``<out>.meta`` sidecar; on resume it validates the sidecar matches and
    raises ValueError otherwise.
    """
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


def load_image_ids(path: Path) -> set[int]:
    """Image IDs already present in a results/checkpoint JSONL file (for resume)."""
    return {int(rec["image_id"]) for rec in iter_results(path)}


async def fetch_manifest_rows(
    db: AsyncSession,
    *,
    status: int | None = ImageStatus.ACTIVE,
    missing_theme: bool = False,
    exclude_existing: bool = False,
    exclude_image_ids: set[int] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return (image_id, filename, ext) rows for images to run inference on.

    Args:
        status: keep only images with this status; None for any status.
        missing_theme: keep only images with no theme tags (has_theme is False).
        exclude_existing: skip images that already have ML suggestion rows.
        exclude_image_ids: post-fetch filter; remove rows whose image_id is in
            this set.  Useful for delta runs: pass IDs already covered by a
            prior results file so they are not re-queued.  None (default) or an
            empty set both mean no filtering.
        limit: cap the number of rows (mainly for smoke tests).
    """
    query = select(Images.image_id, Images.filename, Images.ext)  # type: ignore[call-overload]
    query = query.where(Images.filename.is_not(None))  # type: ignore[union-attr]
    if status is not None:
        query = query.where(Images.status == status)
    if missing_theme:
        query = query.where(Images.has_theme.is_(False))  # type: ignore[attr-defined]
    if exclude_existing:
        existing = select(MlTagSuggestions.image_id)  # type: ignore[call-overload]
        query = query.where(Images.image_id.not_in(existing))  # type: ignore[union-attr]
    query = query.order_by(Images.image_id)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    rows = [{"image_id": row[0], "filename": row[1], "ext": row[2]} for row in result.all()]
    if exclude_image_ids:
        rows = [row for row in rows if row["image_id"] not in exclude_image_ids]
    return rows


@dataclass
class IngestStats:
    """Outcome counters for an ingest run."""

    processed: int = 0
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def ingest_results(
    db: AsyncSession,
    results: Iterable[dict[str, Any]],
    *,
    skip_ids: set[int] | None = None,
    on_processed: Callable[[int], None] | None = None,
) -> IngestStats:
    """Store offline inference results through the shared DB pipeline.

    Each result is ``{"image_id": int, "predictions": [external-tag dicts]}``.
    A failure on one image (e.g. it was deleted since the manifest was built)
    is rolled back and recorded, and the run continues with the rest.
    ``on_processed`` is called with each successfully stored image_id so the
    caller can checkpoint progress.
    """
    stats = IngestStats()
    skip = skip_ids or set()

    for result in results:
        image_id = int(result["image_id"])
        if image_id in skip:
            stats.skipped += 1
            continue
        try:
            stats.created += await store_predictions(db, image_id, result["predictions"])
        except Exception as exc:
            await db.rollback()
            stats.errors.append(f"{image_id}: {exc}")
            logger.error("ml_backfill_ingest_failed", image_id=image_id, error=str(exc))
            continue
        stats.processed += 1
        if on_processed is not None:
            on_processed(image_id)

    return stats

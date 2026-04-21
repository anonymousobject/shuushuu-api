"""R2 operational tooling.

Subcommands:
    split-existing       — one-time move protected images from public bucket to private
    backfill-locations   — one-shot flip r2_location for existing rows (gated)
    reconcile            — heal: upload missing R2 objects from local FS (gated)
    image                — inspect/re-sync a single image
    verify               — audit R2 vs DB state (read-only; --fix flips drifted rows to NONE)
    purge-cache          — manually purge CDN for one image
    health               — report unsynced counts and storage usage (read-only)

Guarded by R2_ENABLED=true (all commands). backfill-locations, reconcile, and
verify --fix additionally require R2_ALLOW_BULK_BACKFILL=true to prevent
staging from mass-touching prod-imported images against its small staging bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

from sqlalchemy import select, update

from app.config import settings
from app.core.database import engine, get_async_session
from app.core.logging import get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2_VARIANTS,
    R2Location,
)
from app.models.image import Images, VariantStatus
from app.services.cloudflare import purge_cache_by_urls

logger = get_logger(__name__)


class R2SyncError(Exception):
    """Base for r2_sync CLI errors."""


class R2DisabledError(R2SyncError):
    """Raised when R2_ENABLED=false."""


class BulkBackfillDisallowedError(R2SyncError):
    """Raised when R2_ALLOW_BULK_BACKFILL=false."""


def require_r2_enabled() -> None:
    if not settings.R2_ENABLED:
        raise R2DisabledError(
            "R2_ENABLED=false. Enable R2 in config before running r2_sync commands."
        )


def require_bulk_backfill() -> None:
    require_r2_enabled()
    if not settings.R2_ALLOW_BULK_BACKFILL:
        raise BulkBackfillDisallowedError(
            "R2_ALLOW_BULK_BACKFILL=false. This command walks the DB for "
            "unsynced rows and uploads local files to R2; on staging this "
            "would mass-upload the prod dataset. Set the flag true only in "
            "prod's steady-state config."
        )


def _positive_int(value: str) -> int:
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {n}")
    return n


def _ready_variants(image: Images) -> list[str]:
    """Variants that should currently exist in R2 for this image.

    Excludes PENDING — those are still being generated and haven't been
    uploaded yet. (Contrast with app.tasks.r2_jobs._expected_variants,
    which includes PENDING so r2_finalize_upload_job waits for them.)
    """
    variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        variants.append("medium")
    if image.large == VariantStatus.READY:
        variants.append("large")
    return variants


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2 operational tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    se = sub.add_parser("split-existing")
    se.add_argument("--dry-run", action="store_true")
    se.add_argument(
        "--concurrency",
        type=_positive_int,
        default=8,
        help="Max images processed in parallel (default: 8).",
    )
    sub.add_parser("backfill-locations")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--stale-after", type=_positive_int, default=600)
    img = sub.add_parser("image")
    img.add_argument("image_id", type=int)
    ver = sub.add_parser(
        "verify",
        description=(
            "Audit R2 vs DB state. Reports missing/unexpected/wrong_bucket "
            "discrepancies. With --fix, flips non-NONE rows back to "
            "r2_location=NONE when their expected object is missing so "
            "reconcile can heal them."
        ),
    )
    ver_scope = ver.add_mutually_exclusive_group(required=True)
    ver_scope.add_argument("--sample", type=_positive_int)
    ver_scope.add_argument("--all", action="store_true")
    ver_scope.add_argument(
        "--since-id",
        type=_positive_int,
        help="Only check rows with image_id >= this value.",
    )
    ver_scope.add_argument(
        "--since-date",
        help="Only check rows with date_added >= this ISO date (e.g. 2026-04-15).",
    )
    ver.add_argument(
        "--concurrency",
        type=_positive_int,
        default=8,
        help="Max images HEADed in parallel (default: 8).",
    )
    ver.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Flip non-NONE rows with missing R2 objects to r2_location=NONE "
            "so the next reconcile run uploads them. Requires "
            "R2_ALLOW_BULK_BACKFILL=true."
        ),
    )
    ver.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix: report would-flip rows without updating the DB.",
    )
    pc = sub.add_parser("purge-cache")
    pc.add_argument("image_id", type=int)
    h = sub.add_parser("health")
    h.add_argument("--json", action="store_true")

    return parser


async def split_existing(*, dry_run: bool, concurrency: int = 8) -> None:
    """Move protected-status images' R2 objects from public to private bucket.

    Assumes existing R2 state is "everything in R2_PUBLIC_BUCKET" (the starting
    point for the production cutover). Idempotent — objects already moved are
    skipped via object_exists checks.

    Shares one aioboto3 client across the whole run (via bulk_session) and
    processes `concurrency` images in parallel.
    """
    r2 = get_r2_storage()
    moved = 0
    errors = 0
    images_seen = 0
    sem = asyncio.Semaphore(concurrency)
    in_flight: set[asyncio.Task[tuple[int, int]]] = set()
    # Cap in-flight task buildup so we don't accumulate thousands of pending
    # coroutines while the semaphore gates actual network work.
    max_in_flight = concurrency * 2
    started_at = time.monotonic()
    last_tick_at = started_at
    last_tick_moved = 0

    logger.info(
        "split_existing_started", dry_run=dry_run, concurrency=concurrency
    )

    async def process_image(image: Images) -> tuple[int, int]:
        """Process one image. Returns (moved_count, error_count)."""
        async with sem:
            variants = _ready_variants(image)
            local_moved = 0
            local_errors = 0
            for variant in variants:
                ext = "webp" if variant == "thumbs" else image.ext
                key = f"{variant}/{image.filename}.{ext}"
                try:
                    if not await r2.object_exists(
                        bucket=settings.R2_PUBLIC_BUCKET, key=key
                    ):
                        continue
                    if dry_run:
                        print(
                            f"DRY_RUN move {settings.R2_PUBLIC_BUCKET}/{key}"
                            f" -> {settings.R2_PRIVATE_BUCKET}/{key}"
                        )
                        local_moved += 1
                        continue
                    await r2.copy_object(
                        src_bucket=settings.R2_PUBLIC_BUCKET,
                        dst_bucket=settings.R2_PRIVATE_BUCKET,
                        key=key,
                    )
                    await r2.delete_object(
                        bucket=settings.R2_PUBLIC_BUCKET, key=key
                    )
                    local_moved += 1
                except Exception as exc:
                    # Isolate per-variant failures — one bad object shouldn't
                    # kill the whole run. Rerunning the script re-tries them.
                    logger.error(
                        "split_existing_move_failed",
                        image_id=image.image_id,
                        key=key,
                        error=repr(exc),
                    )
                    local_errors += 1
            return local_moved, local_errors

    async def drain(*, until: int) -> None:
        """Drain completed tasks until in_flight is below `until`."""
        nonlocal moved, errors
        while len(in_flight) > until:
            done, _ = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                in_flight.discard(task)
                m, e = task.result()
                moved += m
                errors += e

    async with r2.bulk_session():
        async with get_async_session() as db:
            stmt = (
                select(Images)
                .where(Images.status.notin_(PUBLIC_IMAGE_STATUSES_FOR_R2))  # type: ignore[attr-defined]
                .execution_options(yield_per=500)
            )
            stream = await db.stream_scalars(stmt)

            async for image in stream:
                images_seen += 1
                if len(in_flight) >= max_in_flight:
                    await drain(until=max_in_flight - 1)
                in_flight.add(asyncio.create_task(process_image(image)))
                if images_seen % 100 == 0:
                    now = time.monotonic()
                    window = now - last_tick_at
                    recent_rate = (
                        (moved - last_tick_moved) / window if window > 0 else 0.0
                    )
                    overall_rate = (
                        moved / (now - started_at) if now > started_at else 0.0
                    )
                    logger.info(
                        "split_existing_progress",
                        images_seen=images_seen,
                        moved=moved,
                        errors=errors,
                        moves_per_sec=round(recent_rate, 1),
                        avg_moves_per_sec=round(overall_rate, 1),
                    )
                    last_tick_at = now
                    last_tick_moved = moved

            await drain(until=0)

    logger.info(
        "split_existing_completed",
        images_seen=images_seen,
        moved=moved,
        errors=errors,
        dry_run=dry_run,
    )
    print(
        f"{'[dry-run] ' if dry_run else ''}moved {moved} objects"
        f" ({errors} errors) across {images_seen} images"
    )


async def backfill_locations(*, batch_size: int = 1000) -> None:
    """Flip r2_location for rows still at NONE based on current status."""
    require_bulk_backfill()

    total_flipped = 0
    while True:
        async with get_async_session() as db:
            result = await db.execute(
                select(Images).where(Images.r2_location == R2Location.NONE).limit(batch_size)  # type: ignore[arg-type]
            )
            rows = list(result.scalars())
            if not rows:
                break

            public_ids = [
                img.image_id for img in rows if img.status in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]
            private_ids = [
                img.image_id for img in rows if img.status not in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]

            if public_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(public_ids))  # type: ignore[union-attr]
                    .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
                    .values(r2_location=R2Location.PUBLIC)
                )
            if private_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(private_ids))  # type: ignore[union-attr]
                    .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
                    .values(r2_location=R2Location.PRIVATE)
                )

            await db.commit()
            total_flipped += len(public_ids) + len(private_ids)
            logger.info(
                "backfill_batch",
                batch_size=len(rows),
                total_flipped=total_flipped,
            )

    print(f"backfilled {total_flipped} rows")


async def reconcile(*, stale_after: int) -> None:
    """Heal: upload missing R2 objects for unsynced rows older than `stale_after`.

    Idempotent: uploaded variants are re-detected via object_exists on the next
    pass, so a partial run that uploads some variants before failing on a
    missing local file will simply skip those keys next time.
    """
    require_bulk_backfill()

    from datetime import UTC, datetime, timedelta
    from pathlib import Path as FilePath

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=stale_after)
    r2 = get_r2_storage()

    batch_size = 500
    last_image_id = 0
    healed = 0
    processed = 0

    async with r2.bulk_session():
        while True:
            async with get_async_session() as db:
                result = await db.execute(
                    select(Images)
                    .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
                    .where(Images.date_added < cutoff)  # type: ignore[arg-type,operator]
                    .where(Images.image_id > last_image_id)  # type: ignore[arg-type,operator]
                    .order_by(Images.image_id)  # type: ignore[arg-type]
                    .limit(batch_size)
                )
                rows = list(result.scalars())

            if not rows:
                break

            public_healed_ids: list[int] = []
            private_healed_ids: list[int] = []

            for image in rows:
                processed += 1
                last_image_id = image.image_id  # type: ignore[assignment]

                variants = _ready_variants(image)
                bucket = (
                    settings.R2_PUBLIC_BUCKET
                    if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
                    else settings.R2_PRIVATE_BUCKET
                )
                all_uploaded = True
                for variant in variants:
                    ext = "webp" if variant == "thumbs" else image.ext
                    key = f"{variant}/{image.filename}.{ext}"
                    local = FilePath(settings.STORAGE_PATH) / variant / f"{image.filename}.{ext}"
                    if not local.exists():
                        logger.warning("reconcile_local_missing", image_id=image.image_id, variant=variant)
                        all_uploaded = False
                        break
                    if not await r2.object_exists(bucket=bucket, key=key):
                        await r2.upload_file(bucket=bucket, key=key, path=local)

                if all_uploaded:
                    if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2:
                        public_healed_ids.append(image.image_id)  # type: ignore[arg-type]
                    else:
                        private_healed_ids.append(image.image_id)  # type: ignore[arg-type]

            async with get_async_session() as db:
                if public_healed_ids:
                    await db.execute(
                        update(Images)
                        .where(Images.image_id.in_(public_healed_ids))  # type: ignore[union-attr]
                        .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
                        .values(r2_location=R2Location.PUBLIC)
                    )
                if private_healed_ids:
                    await db.execute(
                        update(Images)
                        .where(Images.image_id.in_(private_healed_ids))  # type: ignore[union-attr]
                        .where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
                        .values(r2_location=R2Location.PRIVATE)
                    )
                if public_healed_ids or private_healed_ids:
                    await db.commit()
                    healed += len(public_healed_ids) + len(private_healed_ids)

    print(f"reconciled {healed}/{processed} rows")


async def resync_image(image_id: int) -> None:
    """Debug tool: print current R2 state for one image (read-only)."""
    r2 = get_r2_storage()
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return

    print(
        f"image {image_id} filename={image.filename} status={image.status} "
        f"r2_location={image.r2_location}"
    )
    bucket = (
        settings.R2_PUBLIC_BUCKET
        if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
        else settings.R2_PRIVATE_BUCKET
    )
    for variant in R2_VARIANTS:
        ext = "webp" if variant == "thumbs" else image.ext
        key = f"{variant}/{image.filename}.{ext}"
        exists = await r2.object_exists(bucket=bucket, key=key)
        print(f"  {variant}: {bucket}/{key} exists={exists}")


async def verify(
    *,
    sample: int | None = None,
    since_id: int | None = None,
    since_date: str | None = None,
    concurrency: int = 8,
    fix: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Audit: report DB/R2 discrepancies; optionally heal drifted rows.

    Reports:
      - PUBLIC/PRIVATE rows whose object is missing from expected bucket (`missing`)
      - NONE rows whose object unexpectedly exists in either bucket (`unexpected`)
      - Cross-bucket placement (`wrong_bucket`)

    NONE with no R2 object is legitimate and is NOT reported.

    Scope is exactly one of: sample (last N by image_id desc), since_id,
    since_date, or none (== --all). `--fix` flips `missing`-kind rows to
    r2_location=NONE so the next `reconcile` run uploads them; other
    discrepancy kinds are reported only.

    Uses paginated (image_id > last) batch fetches so each batch runs under a
    short-lived DB connection — a long streaming cursor gets killed by the
    server after a few minutes of HEAD work.
    """
    if fix:
        require_bulk_backfill()

    from datetime import datetime

    scope_predicate: Any = None
    if since_date is not None:
        try:
            scope_date = datetime.fromisoformat(since_date)
        except ValueError as exc:
            raise R2SyncError(
                f"invalid --since-date {since_date!r}: expected ISO format"
                " (e.g. 2026-04-15 or 2026-04-15T00:00:00)"
            ) from exc
        scope_predicate = Images.date_added >= scope_date  # type: ignore[operator]
        scope_desc = f"date_added >= {scope_date.isoformat()}"
    elif since_id is not None:
        scope_desc = f"image_id >= {since_id}"
    elif sample is not None:
        scope_desc = f"sample={sample}"
    else:
        scope_desc = "all"

    r2 = get_r2_storage()
    discrepancies: list[dict[str, Any]] = []
    checked = 0
    flipped = 0
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    logger.info(
        "verify_started",
        scope=scope_desc,
        concurrency=concurrency,
        fix=fix,
        dry_run=dry_run,
    )

    async def check_image(image: Images) -> list[dict[str, Any]]:
        """HEAD each ready variant against both buckets. Return discrepancies."""
        nonlocal errors
        async with sem:
            local_discrepancies: list[dict[str, Any]] = []
            for variant in _ready_variants(image):
                ext = "webp" if variant == "thumbs" else image.ext
                key = f"{variant}/{image.filename}.{ext}"
                try:
                    in_public = await r2.object_exists(
                        bucket=settings.R2_PUBLIC_BUCKET, key=key
                    )
                    in_private = await r2.object_exists(
                        bucket=settings.R2_PRIVATE_BUCKET, key=key
                    )
                except Exception as exc:
                    # Don't report bogus discrepancies on transient HEAD failures.
                    logger.error(
                        "verify_head_failed",
                        image_id=image.image_id,
                        key=key,
                        error=repr(exc),
                    )
                    errors += 1
                    continue
                if image.r2_location == R2Location.NONE:
                    if in_public or in_private:
                        local_discrepancies.append(
                            {
                                "kind": "unexpected",
                                "image_id": image.image_id,
                                "key": key,
                                "found_in_public": in_public,
                                "found_in_private": in_private,
                            }
                        )
                    continue
                expected_bucket = (
                    settings.R2_PUBLIC_BUCKET
                    if image.r2_location == R2Location.PUBLIC
                    else settings.R2_PRIVATE_BUCKET
                )
                found_expected = in_public if image.r2_location == R2Location.PUBLIC else in_private
                found_other = in_private if image.r2_location == R2Location.PUBLIC else in_public
                if not found_expected:
                    local_discrepancies.append(
                        {
                            "kind": "missing",
                            "image_id": image.image_id,
                            "bucket": expected_bucket,
                            "key": key,
                        }
                    )
                if found_other:
                    local_discrepancies.append(
                        {
                            "kind": "wrong_bucket",
                            "image_id": image.image_id,
                            "key": key,
                            "r2_location": int(image.r2_location),
                            "found_in_public": in_public,
                            "found_in_private": in_private,
                        }
                    )
            return local_discrepancies

    async def process_batch(rows: list[Images]) -> None:
        nonlocal checked, flipped
        batch_results = await asyncio.gather(*(check_image(image) for image in rows))
        batch_missing_ids: set[int] = set()
        for row_discrepancies in batch_results:
            discrepancies.extend(row_discrepancies)
            for d in row_discrepancies:
                if d["kind"] == "missing":
                    batch_missing_ids.add(d["image_id"])
        checked += len(rows)

        if fix and batch_missing_ids:
            ordered_ids = sorted(batch_missing_ids)
            if dry_run:
                for image_id in ordered_ids:
                    print(f"DRY_RUN flip image_id={image_id} -> r2_location=NONE")
            else:
                async with get_async_session() as db:
                    await db.execute(
                        update(Images)
                        .where(Images.image_id.in_(ordered_ids))  # type: ignore[union-attr]
                        .where(Images.r2_location != R2Location.NONE)  # type: ignore[arg-type]
                        .values(r2_location=R2Location.NONE)
                    )
                    await db.commit()
            flipped += len(batch_missing_ids)

    async with r2.bulk_session():
        if sample is not None:
            # Small, one-shot read — last N rows by image_id desc.
            async with get_async_session() as db:
                result = await db.execute(
                    select(Images)
                    .order_by(Images.image_id.desc())  # type: ignore[union-attr]
                    .limit(sample)
                )
                rows = list(result.scalars())
            if rows:
                await process_batch(rows)
        else:
            batch_size = 500
            last_image_id = (since_id - 1) if since_id is not None else 0
            while True:
                async with get_async_session() as db:
                    stmt = (
                        select(Images)
                        .where(Images.image_id > last_image_id)  # type: ignore[arg-type,operator]
                        .order_by(Images.image_id)  # type: ignore[arg-type]
                        .limit(batch_size)
                    )
                    if scope_predicate is not None:
                        stmt = stmt.where(scope_predicate)
                    result = await db.execute(stmt)
                    rows = list(result.scalars())

                if not rows:
                    break

                last_image_id = rows[-1].image_id  # type: ignore[assignment]
                await process_batch(rows)
                logger.info(
                    "verify_progress",
                    checked=checked,
                    discrepancies=len(discrepancies),
                    flipped=flipped,
                    errors=errors,
                    last_image_id=last_image_id,
                )

    logger.info(
        "verify_completed",
        checked=checked,
        discrepancies=len(discrepancies),
        flipped=flipped,
        errors=errors,
        fix=fix,
        dry_run=dry_run,
    )
    report: dict[str, Any] = {
        "checked": checked,
        "discrepancies": discrepancies,
        "flipped": flipped,
        "errors": errors,
    }
    suffix = f", flipped {flipped} to NONE" if fix else ""
    print(
        f"checked {checked} rows, {len(discrepancies)} discrepancies"
        f" ({errors} errors){suffix}"
    )
    for d in discrepancies[:20]:
        location = f"{d['bucket']}/{d['key']}" if d.get("bucket") else d["key"]
        print(f"  {d['kind']}: {location} (image_id={d['image_id']})")
    return report


async def purge_cache_command(*, image_id: int) -> None:
    """Manually invoke Cloudflare purge for one image's CDN URLs."""
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return
    variants = _ready_variants(image)
    urls = []
    for variant in variants:
        ext = "webp" if variant == "thumbs" else image.ext
        urls.append(f"{settings.R2_PUBLIC_CDN_URL}/{variant}/{image.filename}.{ext}")
    await purge_cache_by_urls(urls)
    print(f"purged {len(urls)} URLs for image {image_id}")


async def health(*, output_json: bool = False) -> dict[str, Any]:
    """Read-only health report for monitoring wiring."""
    import asyncio as _asyncio
    import subprocess
    from datetime import UTC, datetime

    from sqlalchemy import func

    async with get_async_session() as db:
        count_result = await db.execute(
            select(func.count()).select_from(Images).where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
        )
        unsynced_count = count_result.scalar_one()

        oldest_result = await db.execute(
            select(func.min(Images.date_added)).where(Images.r2_location == R2Location.NONE)  # type: ignore[arg-type]
        )
        oldest = oldest_result.scalar_one_or_none()
        oldest_age = (
            int((datetime.now(UTC).replace(tzinfo=None) - oldest).total_seconds()) if oldest else 0
        )

    try:
        du_output = await _asyncio.to_thread(
            subprocess.check_output, ["du", "-sb", settings.STORAGE_PATH], text=True
        )
        local_bytes = int(du_output.split()[0])
    except Exception:
        local_bytes = -1

    report = {
        "unsynced_count": unsynced_count,
        "oldest_unsynced_age_seconds": oldest_age,
        "local_storage_used_bytes": local_bytes,
        "local_storage_path": settings.STORAGE_PATH,
    }
    if output_json:
        import json

        print(json.dumps(report))
    else:
        for k, v in report.items():
            print(f"{k}: {v}")
    return report


async def _dispatch(args: argparse.Namespace) -> int:
    require_r2_enabled()

    try:
        return await _run(args)
    finally:
        # Close pooled DB connections while the event loop is still running;
        # otherwise aiomysql's Connection.__del__ fires during interpreter
        # shutdown and tries to schedule work on a closed loop.
        await engine.dispose()


async def _run(args: argparse.Namespace) -> int:
    if args.command == "split-existing":
        await split_existing(dry_run=args.dry_run, concurrency=args.concurrency)
    elif args.command == "backfill-locations":
        await backfill_locations()
    elif args.command == "reconcile":
        await reconcile(stale_after=args.stale_after)
    elif args.command == "image":
        await resync_image(args.image_id)
    elif args.command == "verify":
        await verify(
            sample=args.sample,
            since_id=args.since_id,
            since_date=args.since_date,
            concurrency=args.concurrency,
            fix=args.fix,
            dry_run=args.dry_run,
        )
    elif args.command == "purge-cache":
        await purge_cache_command(image_id=args.image_id)
    elif args.command == "health":
        await health(output_json=args.json)
    else:
        raise ValueError(f"unknown command: {args.command}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except R2SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

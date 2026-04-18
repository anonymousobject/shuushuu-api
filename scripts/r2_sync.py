"""R2 operational tooling.

Subcommands:
    split-existing       — one-time move protected images from public bucket to private
    backfill-locations   — one-shot flip r2_location for existing rows (gated)
    reconcile            — heal: upload missing R2 objects from local FS (gated)
    image                — inspect/re-sync a single image
    verify               — audit R2 vs DB state (read-only)
    purge-cache          — manually purge CDN for one image
    health               — report unsynced counts and storage usage (read-only)

Guarded by R2_ENABLED=true (all commands). backfill-locations and reconcile
additionally require R2_ALLOW_BULK_BACKFILL=true to prevent staging from
mass-uploading prod-imported images to its small staging bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import select, update

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import (
    PUBLIC_IMAGE_STATUSES_FOR_R2,
    R2Location,
    R2_VARIANTS,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2 operational tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    se = sub.add_parser("split-existing")
    se.add_argument("--dry-run", action="store_true")
    sub.add_parser("backfill-locations")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--stale-after", type=int, default=600)
    img = sub.add_parser("image")
    img.add_argument("image_id", type=int)
    ver = sub.add_parser("verify")
    ver.add_argument("--sample", type=int, default=None)
    pc = sub.add_parser("purge-cache")
    pc.add_argument("image_id", type=int)
    h = sub.add_parser("health")
    h.add_argument("--json", action="store_true")

    return parser


async def split_existing(*, dry_run: bool) -> None:
    """Move protected-status images' R2 objects from public to private bucket.

    Assumes existing R2 state is "everything in R2_PUBLIC_BUCKET" (the starting
    point for the production cutover). Idempotent — objects already moved are
    skipped via object_exists checks.
    """
    r2 = get_r2_storage()

    async with get_async_session() as db:
        result = await db.execute(
            select(Images).where(Images.status.notin_(PUBLIC_IMAGE_STATUSES_FOR_R2))
        )
        rows = list(result.scalars())

    logger.info("split_existing_started", count=len(rows), dry_run=dry_run)

    moved = 0
    for image in rows:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")
        for variant in variants:
            ext = "webp" if variant == "thumbs" else image.ext
            key = f"{variant}/{image.filename}.{ext}"
            if not await r2.object_exists(bucket=settings.R2_PUBLIC_BUCKET, key=key):
                continue
            if dry_run:
                print(
                    f"DRY_RUN move {settings.R2_PUBLIC_BUCKET}/{key}"
                    f" -> {settings.R2_PRIVATE_BUCKET}/{key}"
                )
                moved += 1
                continue
            await r2.copy_object(
                src_bucket=settings.R2_PUBLIC_BUCKET,
                dst_bucket=settings.R2_PRIVATE_BUCKET,
                key=key,
            )
            await r2.delete_object(bucket=settings.R2_PUBLIC_BUCKET, key=key)
            moved += 1

    logger.info("split_existing_completed", moved=moved, dry_run=dry_run)
    print(f"{'[dry-run] ' if dry_run else ''}moved {moved} objects")


async def backfill_locations(*, batch_size: int = 1000) -> None:
    """Flip r2_location for rows still at NONE based on current status."""
    require_bulk_backfill()

    total_flipped = 0
    while True:
        async with get_async_session() as db:
            result = await db.execute(
                select(Images)
                .where(Images.r2_location == R2Location.NONE)
                .limit(batch_size)
            )
            rows = list(result.scalars())
            if not rows:
                break

            public_ids = [
                img.image_id
                for img in rows
                if img.status in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]
            private_ids = [
                img.image_id
                for img in rows
                if img.status not in PUBLIC_IMAGE_STATUSES_FOR_R2
            ]

            if public_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(public_ids))
                    .where(Images.r2_location == R2Location.NONE)
                    .values(r2_location=R2Location.PUBLIC)
                )
            if private_ids:
                await db.execute(
                    update(Images)
                    .where(Images.image_id.in_(private_ids))
                    .where(Images.r2_location == R2Location.NONE)
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

    async with get_async_session() as db:
        result = await db.execute(
            select(Images)
            .where(Images.r2_location == R2Location.NONE)
            .where(Images.date_added < cutoff)
        )
        rows = list(result.scalars())

    healed = 0
    for image in rows:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")
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
            new_location = (
                R2Location.PUBLIC
                if image.status in PUBLIC_IMAGE_STATUSES_FOR_R2
                else R2Location.PRIVATE
            )
            async with get_async_session() as db:
                await db.execute(
                    update(Images)
                    .where(Images.image_id == image.image_id)
                    .where(Images.r2_location == R2Location.NONE)
                    .values(r2_location=new_location)
                )
                await db.commit()
            healed += 1

    print(f"reconciled {healed}/{len(rows)} rows")


async def resync_image(image_id: int) -> None:
    """Debug tool: print current R2 state for one image (read-only)."""
    r2 = get_r2_storage()
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return

    print(f"image {image_id} filename={image.filename} status={image.status} "
          f"r2_location={image.r2_location}")
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


async def verify(*, sample: int | None) -> dict[str, Any]:
    """Audit: report DB/R2 discrepancies.

    Reports:
      - PUBLIC/PRIVATE rows whose object is missing from expected bucket (`missing`)
      - NONE rows whose object unexpectedly exists in either bucket (`unexpected`)
      - Cross-bucket placement (`wrong_bucket`)

    NONE with no R2 object is legitimate and is NOT reported.
    """
    r2 = get_r2_storage()
    discrepancies: list[dict[str, Any]] = []

    async with get_async_session() as db:
        stmt = select(Images)
        if sample:
            stmt = stmt.order_by(Images.image_id.desc()).limit(sample)
        result = await db.execute(stmt)
        rows = list(result.scalars())

    for image in rows:
        variants = ["fullsize", "thumbs"]
        if image.medium == VariantStatus.READY:
            variants.append("medium")
        if image.large == VariantStatus.READY:
            variants.append("large")

        for variant in variants:
            ext = "webp" if variant == "thumbs" else image.ext
            key = f"{variant}/{image.filename}.{ext}"
            in_public = await r2.object_exists(
                bucket=settings.R2_PUBLIC_BUCKET, key=key
            )
            in_private = await r2.object_exists(
                bucket=settings.R2_PRIVATE_BUCKET, key=key
            )
            if image.r2_location == R2Location.NONE:
                if in_public or in_private:
                    discrepancies.append({
                        "kind": "unexpected",
                        "image_id": image.image_id,
                        "key": key,
                        "found_in_public": in_public,
                        "found_in_private": in_private,
                    })
                continue
            expected_bucket = (
                settings.R2_PUBLIC_BUCKET
                if image.r2_location == R2Location.PUBLIC
                else settings.R2_PRIVATE_BUCKET
            )
            found_expected = (
                in_public if image.r2_location == R2Location.PUBLIC else in_private
            )
            found_other = (
                in_private if image.r2_location == R2Location.PUBLIC else in_public
            )
            if not found_expected:
                discrepancies.append({
                    "kind": "missing",
                    "image_id": image.image_id,
                    "bucket": expected_bucket,
                    "key": key,
                })
            if found_other:
                discrepancies.append({
                    "kind": "wrong_bucket",
                    "image_id": image.image_id,
                    "key": key,
                    "r2_location": int(image.r2_location),
                    "found_in_public": in_public,
                    "found_in_private": in_private,
                })

    report = {"checked": len(rows), "discrepancies": discrepancies}
    print(f"checked {report['checked']} rows, {len(discrepancies)} discrepancies")
    for d in discrepancies[:20]:
        print(f"  {d['kind']}: {d.get('bucket', '')}{d['key']} (image_id={d['image_id']})")
    return report


async def purge_cache_command(*, image_id: int) -> None:
    """Manually invoke Cloudflare purge for one image's CDN URLs."""
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))
        image = result.scalar_one_or_none()
    if image is None:
        print(f"image {image_id} not found")
        return
    variants = ["fullsize", "thumbs"]
    if image.medium == VariantStatus.READY:
        variants.append("medium")
    if image.large == VariantStatus.READY:
        variants.append("large")
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
            select(func.count()).select_from(Images).where(Images.r2_location == R2Location.NONE)
        )
        unsynced_count = count_result.scalar_one()

        oldest_result = await db.execute(
            select(func.min(Images.date_added)).where(Images.r2_location == R2Location.NONE)
        )
        oldest = oldest_result.scalar_one_or_none()
        oldest_age = (
            int((datetime.now(UTC).replace(tzinfo=None) - oldest).total_seconds())
            if oldest
            else 0
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

    if args.command == "split-existing":
        await split_existing(dry_run=args.dry_run)
    elif args.command == "backfill-locations":
        await backfill_locations()
    elif args.command == "reconcile":
        await reconcile(stale_after=args.stale_after)
    elif args.command == "image":
        await resync_image(args.image_id)
    elif args.command == "verify":
        await verify(sample=args.sample)
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

#!/usr/bin/env python3
"""
Rebuild thumbs for palette sources (GIF, pngquant PNG) with a transparent index.

Until PR #409, app.services.image_processing.create_thumbnail converted mode
"P" sources straight to RGB, which paints every transparent pixel with the
transparent index's colour (old GIF tooling likes #FF00FF; image 1003779 was
the report). Medium/large variants reuse the source format and keep the
palette, so only thumbs are rebuilt. No variant statuses change.

Same three phases as scripts/regen_icc_png_media.py, whose scan loop, progress
files and batch runner this script reuses:

    # 1. Scan every GIF/PNG row's fullsize header (mode + transparency key
    #    only; no pixel decode) and write the affected image ids, one per line.
    uv run python scripts/regen_palette_thumbs.py scan [--min-id N] [--output palette-thumb-candidates]

    # 2. Regenerate thumbs. Appends each finished id to <candidates>.done so an
    #    interrupted run resumes where it left off. Re-uploads the thumb to R2
    #    for rows already synced when R2_ENABLED.
    uv run python scripts/regen_palette_thumbs.py fix [--candidates palette-thumb-candidates] [--limit N] [--dry-run]

    # 3. When the fix ran on a box that has the files but not R2 (dev), push
    #    the regenerated thumbs to R2 from there: run with DATABASE_URL, R2_*
    #    and CLOUDFLARE_* pointed at prod and STORAGE_PATH at the local files.
    #    Appends finished ids to <done>.synced.
    uv run python scripts/regen_palette_thumbs.py sync [--done palette-thumb-candidates.done] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image
from sqlalchemy import func, select
from sqlmodel import col

from app.config import settings
from app.core.database import get_async_session
from app.core.r2_constants import R2Location
from app.models.image import Images
from app.services.image_processing import create_thumbnail
from scripts.r2_sync import force_reupload_image
from scripts.regen_icc_png_media import FixOutcome, pending_ids, run_batch, scan

THUMBS_ONLY = {"thumbs"}


def is_palette_transparent(path: Path) -> bool:
    """True when the file opens in mode "P" with a transparency key.

    Reads the header only; Image.open is lazy and does not decode pixels, so
    Pillow's decompression-bomb pixel ceiling is lifted for the duration of
    the check (prod has legitimate images past the default 178M-pixel limit).
    """
    pixel_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as img:
            return img.mode == "P" and "transparency" in img.info
    finally:
        Image.MAX_IMAGE_PIXELS = pixel_limit


async def fetch_rows(*, min_id: int | None) -> list[tuple[int, str, str]]:
    """All GIF and PNG image rows as (image_id, filename, ext), ascending by id."""
    query = select(Images.image_id, Images.filename, Images.ext).where(  # type: ignore[call-overload]
        func.lower(col(Images.ext)).in_(["gif", "png"])
    )
    if min_id is not None:
        query = query.where(col(Images.image_id) >= min_id)
    query = query.order_by(col(Images.image_id))
    async with get_async_session() as db:
        result = await db.execute(query)
        return [(row[0], row[1], row[2]) for row in result.all()]


async def fix_image(image_id: int, *, dry_run: bool) -> FixOutcome:
    """Regenerate one image's thumb and re-sync it to R2 if the row is synced."""
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
    if image is None:
        return FixOutcome(image_id, False, "not found")

    source = Path(settings.STORAGE_PATH) / "fullsize" / f"{image.filename}.{image.ext}"
    if not source.exists():
        return FixOutcome(image_id, False, f"source not found: {source}")

    reupload = settings.R2_ENABLED and image.r2_location != R2Location.NONE
    if dry_run:
        return FixOutcome(
            image_id, True, "would regenerate" + (" + R2 reupload" if reupload else "")
        )

    await asyncio.to_thread(create_thumbnail, source, image_id, image.ext, settings.STORAGE_PATH)
    if reupload:
        await force_reupload_image(image_id=image_id, dry_run=dry_run, only=THUMBS_ONLY)
    return FixOutcome(image_id, True, "regenerated" + (" + R2 reupload" if reupload else ""))


async def sync_image(image_id: int, *, dry_run: bool) -> FixOutcome:
    """Push one image's fix-regenerated thumb to R2.

    The row is read from whatever DB this process points at (prod, when
    syncing from the dev box); the thumb comes from local disk.
    """
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
    if image is None:
        return FixOutcome(image_id, False, "not found")
    if image.r2_location == R2Location.NONE:
        return FixOutcome(image_id, False, "r2_location=NONE; not this script's job")
    if not (Path(settings.STORAGE_PATH) / "thumbs" / f"{image.filename}.webp").exists():
        return FixOutcome(image_id, False, "local thumb missing; was fix run here?")

    await force_reupload_image(image_id=image_id, dry_run=dry_run, only=THUMBS_ONLY)
    return FixOutcome(image_id, True, "would sync" if dry_run else "synced")


async def cmd_scan(*, min_id: int | None, output: Path) -> int:
    print(f"Storage path: {settings.STORAGE_PATH}")
    print("Fetching GIF/PNG rows...")
    rows = await fetch_rows(min_id=min_id)
    print(f"Scanning {len(rows):,} sources...")
    result = scan(rows, settings.STORAGE_PATH, is_palette_transparent)
    output.write_text("".join(f"{image_id}\n" for image_id in result.candidates))
    print("-" * 80)
    print(f"Scanned:   {result.scanned:,}")
    print(f"Affected:  {len(result.candidates):,}  -> {output}")
    print(f"Missing:   {len(result.missing):,}")
    print(f"Unreadable: {len(result.unreadable):,}")
    for image_id in result.missing:
        print(f"  MISSING SOURCE: image {image_id}", file=sys.stderr)
    for image_id, error in result.unreadable:
        print(f"  UNREADABLE: image {image_id}: {error}", file=sys.stderr)
    return 0


async def cmd_fix(*, candidates: Path, limit: int | None, dry_run: bool) -> int:
    done_file = candidates.with_suffix(candidates.suffix + ".done")
    print(f"Storage path: {settings.STORAGE_PATH} | R2: {settings.R2_ENABLED} | Dry run: {dry_run}")
    return await run_batch(
        ids=pending_ids(candidates, done_file)[:limit],
        progress_file=done_file,
        dry_run=dry_run,
        action=lambda image_id: fix_image(image_id, dry_run=dry_run),
    )


async def cmd_sync(*, done: Path, limit: int | None, dry_run: bool) -> int:
    synced_file = done.with_suffix(done.suffix + ".synced")
    print(
        f"Storage path: {settings.STORAGE_PATH} | "
        f"R2 buckets: {settings.R2_PUBLIC_BUCKET} / {settings.R2_PRIVATE_BUCKET} | Dry run: {dry_run}"
    )
    return await run_batch(
        ids=pending_ids(done, synced_file)[:limit],
        progress_file=synced_file,
        dry_run=dry_run,
        action=lambda image_id: sync_image(image_id, dry_run=dry_run),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="find affected sources, write candidate ids")
    scan_parser.add_argument(
        "--min-id", type=int, default=None, help="only rows with image_id >= N"
    )
    scan_parser.add_argument("--output", type=Path, default=Path("palette-thumb-candidates"))

    fix_parser = sub.add_parser("fix", help="regenerate thumbs for candidate ids")
    fix_parser.add_argument("--candidates", type=Path, default=Path("palette-thumb-candidates"))
    fix_parser.add_argument("--limit", type=int, default=None, help="stop after N images")
    fix_parser.add_argument("--dry-run", action="store_true")

    sync_parser = sub.add_parser("sync", help="push fixed thumbs to R2")
    sync_parser.add_argument("--done", type=Path, default=Path("palette-thumb-candidates.done"))
    sync_parser.add_argument("--limit", type=int, default=None, help="stop after N images")
    sync_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "scan":
        return asyncio.run(cmd_scan(min_id=args.min_id, output=args.output))
    if args.command == "sync":
        if not settings.R2_ENABLED:
            print(
                "ERROR: sync needs R2_ENABLED=true (point R2_* and DATABASE_URL at prod)",
                file=sys.stderr,
            )
            return 1
        if not args.done.exists():
            print(f"ERROR: done file not found: {args.done}", file=sys.stderr)
            return 1
        return asyncio.run(cmd_sync(done=args.done, limit=args.limit, dry_run=args.dry_run))
    if not args.candidates.exists():
        print(f"ERROR: candidates file not found: {args.candidates}", file=sys.stderr)
        return 1
    return asyncio.run(cmd_fix(candidates=args.candidates, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())

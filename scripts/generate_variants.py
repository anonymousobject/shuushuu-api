#!/usr/bin/env python3
"""
Generate medium/large variants for existing images in the database.

Finds images where medium=1 or large=1 in the database but the variant file
is missing on disk, and regenerates them.

Uses the image's stored filename (e.g., 2025-12-29-1112174) for the output
file, matching what the media endpoint expects for X-Accel-Redirect.

Usage:
    # Dry run (shows what would be generated)
    uv run python scripts/generate_variants.py --missing-only --dry-run

    # Generate missing variants
    uv run python scripts/generate_variants.py --missing-only

    # Generate all variants (regenerate existing too)
    uv run python scripts/generate_variants.py --all

    # Control parallelism
    uv run python scripts/generate_variants.py --missing-only --workers 8
"""

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def process_variant_worker(
    args: tuple[int, str, str, str, int, int, str, bool, bool],
) -> tuple[int, str, bool, str]:
    """Worker function for multiprocessing.

    Args:
        args: Tuple of (image_id, filename, ext, storage_path, width, height,
              variant_type, dry_run, missing_only)

    Returns:
        Tuple of (image_id, variant_type, success, message)
    """
    image_id, filename, ext, storage_path, width, height, variant_type, dry_run, missing_only = args

    variant_path = Path(storage_path) / variant_type / f"{filename}.{ext}"
    source_path = Path(storage_path) / "fullsize" / f"{filename}.{ext}"

    if missing_only and variant_path.exists():
        return image_id, variant_type, True, "skipped"

    if not source_path.exists():
        return image_id, variant_type, False, f"source not found: {source_path}"

    if dry_run:
        return image_id, variant_type, True, "would create"

    try:
        from PIL import Image, ImageCms
        from PIL.ImageCms import PyCMSError

        from app.config import settings

        srgb_profile = ImageCms.createProfile("sRGB")

        if variant_type == "medium":
            threshold = settings.MEDIUM_EDGE
        else:
            threshold = settings.LARGE_EDGE

        variant_dir = Path(storage_path) / variant_type
        variant_dir.mkdir(parents=True, exist_ok=True)

        with Image.open(source_path) as img:
            # Convert to sRGB
            try:
                icc_profile = img.info.get("icc_profile")
                if icc_profile:
                    input_profile = ImageCms.ImageCmsProfile(
                        ImageCms.getOpenProfile(icc_profile)
                    )
                    if img.mode == "L":
                        img = ImageCms.profileToProfile(img, input_profile, srgb_profile)
                    else:
                        img = ImageCms.profileToProfile(
                            img, input_profile, srgb_profile, outputMode="RGB"
                        )
            except (PyCMSError, OSError, TypeError):
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

            # Convert RGBA to RGB for JPEG
            if img.mode in ("RGBA", "LA") and ext.lower() in ("jpg", "jpeg"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background

            img.thumbnail((threshold, threshold), Image.Resampling.LANCZOS)

            save_kwargs = {}
            if ext.lower() in ("jpg", "jpeg"):
                save_kwargs["quality"] = settings.LARGE_QUALITY
                save_kwargs["optimize"] = True
            elif ext.lower() == "webp":
                save_kwargs["quality"] = settings.LARGE_QUALITY

            img.save(variant_path, **save_kwargs)

            # Delete if variant is larger than original
            original_size = source_path.stat().st_size
            variant_size = variant_path.stat().st_size
            if variant_size >= original_size:
                variant_path.unlink()
                return image_id, variant_type, True, "deleted (larger than original)"

            return image_id, variant_type, True, f"created ({variant_size:,} bytes)"

    except Exception as e:
        return image_id, variant_type, False, f"error: {e}"


def get_images_needing_variants(
    missing_only: bool = True,
    all_images: bool = False,
) -> list[tuple[int, str, str, int, int, int, int]]:
    """Fetch images that need variant generation.

    Returns:
        List of (image_id, filename, ext, width, height, medium, large)
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings

    async def _fetch() -> list[tuple[int, str, str, int, int, int, int]]:
        engine = create_async_engine(settings.DATABASE_URL, echo=False)
        try:
            async with engine.connect() as conn:
                if all_images:
                    query = "SELECT image_id, filename, ext, width, height, medium, `large` FROM images WHERE medium = 1 OR `large` = 1 ORDER BY image_id"
                else:
                    query = "SELECT image_id, filename, ext, width, height, medium, `large` FROM images WHERE medium = 1 OR `large` = 1 ORDER BY image_id"

                result = await conn.execute(text(query))
                return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in result.fetchall()]
        finally:
            await engine.dispose()

    return asyncio.run(_fetch())


def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate medium/large variants for images"
    )
    parser.add_argument("--all", action="store_true", dest="all_images",
                        help="Process all images with medium/large flags")
    parser.add_argument("--missing-only", action="store_true",
                        help="Only generate variants where file is missing on disk")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"Number of parallel workers (default: {os.cpu_count() or 4})")

    args = parser.parse_args()

    if not args.all_images and not args.missing_only:
        parser.error("Must specify --all or --missing-only")

    from app.config import settings

    workers = args.workers or os.cpu_count() or 4

    print("=" * 80)
    print("Variant Generation Script (medium/large)")
    print("=" * 80)
    print(f"Storage Path: {settings.STORAGE_PATH}")
    print(f"Workers: {workers}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Missing Only: {args.missing_only}")
    print("=" * 80)

    print("\nFetching image metadata...")
    images = get_images_needing_variants(
        missing_only=args.missing_only,
        all_images=args.all_images,
    )
    print(f"Found {len(images):,} images with medium/large flags")

    # Build work items for both medium and large variants
    work_items = []
    for image_id, filename, ext, width, height, has_medium, has_large in images:
        if has_medium:
            work_items.append((
                image_id, filename, ext, settings.STORAGE_PATH,
                width, height, "medium", args.dry_run, args.missing_only,
            ))
        if has_large:
            work_items.append((
                image_id, filename, ext, settings.STORAGE_PATH,
                width, height, "large", args.dry_run, args.missing_only,
            ))

    print(f"Total variant jobs: {len(work_items):,}")

    if not work_items:
        print("Nothing to do.")
        return

    print("\nProcessing...")
    print("-" * 80)

    success_count = 0
    error_count = 0
    skipped_count = 0
    created_count = 0
    processed = 0
    total = len(work_items)
    start_time = time.time()
    last_report_time = start_time

    with multiprocessing.Pool(processes=workers) as pool:
        for image_id, variant_type, success, message in pool.imap_unordered(
            process_variant_worker, work_items, chunksize=100
        ):
            processed += 1

            if success:
                if "skipped" in message:
                    skipped_count += 1
                else:
                    success_count += 1
                    if "created" in message:
                        created_count += 1
            else:
                error_count += 1
                print(f"  ERROR {variant_type} {image_id}: {message}", flush=True)

            current_time = time.time()
            if current_time - last_report_time >= 5:
                elapsed = current_time - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                pct = processed / total * 100
                print(
                    f"Progress: {processed:,}/{total:,} ({pct:.1f}%) | "
                    f"Rate: {rate:.1f}/s | ETA: {format_time(remaining)} | "
                    f"Created: {created_count:,} Skip: {skipped_count:,} Err: {error_count:,}",
                    flush=True,
                )
                last_report_time = current_time

    elapsed = time.time() - start_time
    rate = processed / elapsed if elapsed > 0 else 0

    print("-" * 80)
    print(f"\nSUMMARY")
    print("=" * 80)
    print(f"Total jobs:    {total:,}")
    print(f"Created:       {created_count:,}")
    print(f"Skipped:       {skipped_count:,}")
    print(f"Errors:        {error_count:,}")
    print(f"Time elapsed:  {format_time(elapsed)}")
    print(f"Rate:          {rate:.1f}/s")
    print("=" * 80)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

#!/usr/bin/env python3
"""
Generate thumbnails for existing images in the database.

Optimized for processing large volumes (1M+ images) with:
- Multiprocessing for parallel thumbnail generation
- Streaming database fetches to minimize memory usage
- Progress tracking with ETA
- Resumable via --missing-only flag

Usage:
    # Generate thumbnails for specific image IDs
    uv run python scripts/generate_thumbnails.py 123 456 789

    # Generate thumbnails for all images (uses all CPU cores)
    uv run python scripts/generate_thumbnails.py --all

    # Control parallelism (default: number of CPU cores)
    uv run python scripts/generate_thumbnails.py --all --workers 8

    # Only process images missing thumbnails (resumable)
    uv run python scripts/generate_thumbnails.py --all --missing-only

    # Dry run to see what would be done
    uv run python scripts/generate_thumbnails.py --all --dry-run

    # Run in background and log output to file
    nohup uv run python scripts/generate_thumbnails.py --all --missing-only --workers 6 > thumb_gen.log 2>&1 &

    # Monitor progress
    tail -f thumb_gen.log | grep "Progress:"

    # Stop gracefully (allows current work to finish)
    pkill -TERM -f "generate_thumbnails.py"
"""

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Iterator

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))


def get_source_path(filename: str, ext: str, storage_path: str) -> Path:
    """Construct the fullsize image path from image metadata."""
    return Path(storage_path) / "fullsize" / f"{filename}.{ext}"


def get_thumbnail_path(filename: str, storage_path: str) -> Path:
    """Construct the thumbnail path from image metadata."""
    return Path(storage_path) / "thumbs" / f"{filename}.webp"


def process_thumbnail_worker(args: tuple[int, str, str, str, bool, bool]) -> tuple[int, bool, str]:
    """Worker function for multiprocessing.

    Args:
        args: Tuple of (image_id, filename, ext, storage_path, dry_run, missing_only)

    Returns:
        Tuple of (image_id, success, message)
    """
    image_id, filename, ext, storage_path, dry_run, missing_only = args

    source_path = get_source_path(filename, ext, storage_path)
    thumb_path = get_thumbnail_path(filename, storage_path)

    # Check if source exists
    if not source_path.exists():
        return image_id, False, f"Source not found: {source_path}"

    # Check if thumbnail already exists
    if missing_only and thumb_path.exists():
        return image_id, True, "skipped"

    if dry_run:
        return image_id, True, "would create"

    # Generate the thumbnail - import here to avoid issues with multiprocessing
    try:
        from app.services.image_processing import create_thumbnail

        create_thumbnail(
            source_path=source_path,
            image_id=image_id,
            ext=ext,
            storage_path=storage_path,
        )

        # Verify thumbnail was created
        if thumb_path.exists():
            size = thumb_path.stat().st_size
            return image_id, True, f"created ({size:,} bytes)"
        else:
            return image_id, False, "creation failed"

    except Exception as e:
        return image_id, False, f"error: {e}"


def get_images_streaming(
    image_ids: list[int] | None = None,
    all_images: bool = False,
    batch_size: int = 10000,
) -> Iterator[tuple[int, str, str]]:
    """Stream images from database in batches to minimize memory usage.

    Yields:
        Tuples of (image_id, filename, ext)
    """
    # Import here to avoid issues at module load time
    import asyncio

    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings
    from app.models import Images

    async def _fetch_images() -> list[tuple[int, str, str]]:
        engine = create_async_engine(settings.DATABASE_URL, echo=False)

        async with engine.connect() as conn:
            if image_ids:
                # Fetch specific images
                result = await conn.execute(
                    select(Images.image_id, Images.filename, Images.ext)  # type: ignore[call-overload]
                    .where(Images.image_id.in_(image_ids))  # type: ignore[union-attr]
                    .order_by(Images.image_id)
                )
                return [(r[0], r[1], r[2]) for r in result.fetchall()]

            elif all_images:
                # Stream all images in batches using keyset pagination (newest first)
                all_results: list[tuple[int, str, str]] = []
                last_id = 2**31  # Start high, work backwards

                while True:
                    result = await conn.execute(
                        select(Images.image_id, Images.filename, Images.ext)  # type: ignore[call-overload]
                        .where(Images.status == 1)  # type: ignore[arg-type]
                        .where(Images.image_id < last_id)  # type: ignore[arg-type,operator]
                        .order_by(Images.image_id.desc())  # type: ignore[union-attr]
                        .limit(batch_size)
                    )
                    batch = result.fetchall()

                    if not batch:
                        break

                    for row in batch:
                        all_results.append((row[0], row[1], row[2]))

                    last_id = batch[-1][0]

                return all_results

        return []

    # Run async function and yield results
    results = asyncio.run(_fetch_images())
    yield from results


def get_image_count(all_images: bool = False, image_ids: list[int] | None = None) -> int:
    """Get total count of images to process."""
    if image_ids:
        return len(image_ids)

    import asyncio

    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings
    from app.models import Images

    async def _count() -> int:
        engine = create_async_engine(settings.DATABASE_URL, echo=False)
        async with engine.connect() as conn:
            result = await conn.execute(
                select(func.count()).select_from(Images).where(Images.status == 1)  # type: ignore[arg-type]
            )
            return result.scalar() or 0

    return asyncio.run(_count())


def format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def generate_thumbnails(
    image_ids: list[int] | None = None,
    all_images: bool = False,
    dry_run: bool = False,
    missing_only: bool = False,
    workers: int | None = None,
) -> None:
    """Main function to generate thumbnails with parallel processing."""

    from app.config import settings

    # Determine worker count
    if workers is None:
        workers = os.cpu_count() or 4

    print("=" * 80)
    print("Thumbnail Generation Script (Parallel)")
    print("=" * 80)
    print(f"Storage Path: {settings.STORAGE_PATH}")
    print(f"Workers: {workers}")
    print(f"Dry Run: {dry_run}")
    print(f"Missing Only: {missing_only}")
    print("=" * 80)

    # Get total count first
    print("\nCounting images...")
    total_images = get_image_count(all_images=all_images, image_ids=image_ids)
    print(f"Found {total_images:,} images to process")

    if total_images == 0:
        print("No images to process. Exiting.")
        return

    if dry_run:
        print("\n[DRY RUN MODE - No changes will be made]\n")

    # Prepare work items
    print("\nFetching image metadata...")
    work_items = [
        (image_id, filename, ext, settings.STORAGE_PATH, dry_run, missing_only)
        for image_id, filename, ext in get_images_streaming(
            image_ids=image_ids, all_images=all_images
        )
    ]

    print(f"Loaded {len(work_items):,} work items")
    print("\nProcessing images...")
    print("-" * 80)

    success_count = 0
    error_count = 0
    skipped_count = 0
    processed = 0
    start_time = time.time()
    last_report_time = start_time

    # Process with multiprocessing pool using imap for memory efficiency
    # imap processes items lazily instead of submitting all 1M+ at once
    with multiprocessing.Pool(processes=workers) as pool:
        # Use imap_unordered for better performance (order doesn't matter)
        for image_id, success, message in pool.imap_unordered(
            process_thumbnail_worker, work_items, chunksize=100
        ):
            processed += 1

            if success:
                if "skipped" in message:
                    skipped_count += 1
                else:
                    success_count += 1
            else:
                error_count += 1
                print(f"  ERROR Image {image_id}: {message}", flush=True)

            # Progress report every 5 seconds
            current_time = time.time()
            if current_time - last_report_time >= 5:
                elapsed = current_time - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total_images - processed) / rate if rate > 0 else 0
                pct = processed / total_images * 100

                print(
                    f"Progress: {processed:,}/{total_images:,} ({pct:.1f}%) | "
                    f"Rate: {rate:.1f}/s | "
                    f"ETA: {format_time(remaining)} | "
                    f"OK: {success_count:,} Skip: {skipped_count:,} Err: {error_count:,}",
                    flush=True,
                )
                last_report_time = current_time

    # Final summary
    elapsed = time.time() - start_time
    rate = processed / elapsed if elapsed > 0 else 0

    print("-" * 80)
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total images processed: {total_images:,}")
    print(f"Successfully generated: {success_count:,}")
    print(f"Skipped (already exist): {skipped_count:,}")
    print(f"Errors:                 {error_count:,}")
    print(f"Time elapsed:           {format_time(elapsed)}")
    print(f"Average rate:           {rate:.1f} images/sec")

    if dry_run:
        print("\n[DRY RUN - no changes were made]")
    else:
        print("\nThumbnail generation complete!")

    print("=" * 80)

    # Exit with error code if there were errors
    if error_count > 0:
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate thumbnails for existing images in the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate thumbnails for specific images
  uv run python scripts/generate_thumbnails.py 123 456 789

  # Generate thumbnails for all images (uses all CPU cores)
  uv run python scripts/generate_thumbnails.py --all

  # Control parallelism
  uv run python scripts/generate_thumbnails.py --all --workers 8

  # Generate thumbnails only for images missing them (resumable)
  uv run python scripts/generate_thumbnails.py --all --missing-only

  # Dry run to see what would be done
  uv run python scripts/generate_thumbnails.py --all --dry-run
        """,
    )

    parser.add_argument(
        "image_ids",
        nargs="*",
        type=int,
        help="Specific image IDs to generate thumbnails for",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_images",
        help="Generate thumbnails for all active images in the database",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate thumbnails for images that don't have one",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Number of parallel workers (default: {os.cpu_count() or 4} CPU cores)",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.image_ids and not args.all_images:
        parser.error("Must specify either image IDs or --all flag")

    if args.image_ids and args.all_images:
        parser.error("Cannot specify both image IDs and --all flag")

    try:
        generate_thumbnails(
            image_ids=args.image_ids if args.image_ids else None,
            all_images=args.all_images,
            dry_run=args.dry_run,
            missing_only=args.missing_only,
            workers=args.workers,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(130)


if __name__ == "__main__":
    # Required for multiprocessing on some platforms
    multiprocessing.freeze_support()
    main()

#!/usr/bin/env python3
"""
Populate iqdb-rs with image thumbnails AND capture their signature hashes
into images.iqdb_hash.

Originally a one-shot bootstrap; now also the backfill source for the
iqdb_hash column. Run with --only-missing-hash for the cutover backfill.

This script also heals broken iqdb entries: rows whose original
add_to_iqdb_job exhausted retries have iqdb_hash IS NULL AND no entry in
iqdb-rs; re-POSTing here populates both.

Usage:
    uv run python scripts/populate_iqdb.py [options]

Options:
    --batch-size N         Process N images per DB batch (default: 100)
    --concurrency N        Max concurrent iqdb POSTs per batch (default: 50)
    --dry-run              Show what would be done without making changes
    --skip-missing         Skip images with missing thumbnails
    --start-from ID        Start from this image_id (resume helper)
    --only-missing-hash    Process only rows where iqdb_hash IS NULL
                           (default mode for cutover backfill — re-runs
                           skip already-populated rows)
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.models import Images


async def check_iqdb_available() -> bool:
    """Check if IQDB server is reachable."""
    try:
        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/status"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(iqdb_url)
            return response.status_code in (200, 404)  # 404 is OK, means server is up
    except (httpx.RequestError, httpx.TimeoutException):
        return False


def add_image_to_iqdb(
    client: httpx.Client, image_id: int, thumb_path: Path
) -> tuple[bool, str, str | None]:
    """Add a single image to IQDB.

    Returns:
        tuple: (success: bool, message: str, iqdb_hash: str | None)
    """
    try:
        if not thumb_path.exists():
            return False, f"Thumbnail not found: {thumb_path}", None

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        with open(thumb_path, "rb") as f:
            files = {"file": (thumb_path.name, f, "image/jpeg")}
            response = client.post(iqdb_url, files=files)

        if response.status_code in (200, 201):
            try:
                iqdb_hash = response.json()["hash"]
            except (ValueError, KeyError, TypeError) as e:
                return False, f"hash parse failed: {e}", None
            return True, "OK", iqdb_hash
        return (
            False,
            f"HTTP {response.status_code}: {response.text[:100]}",
            None,
        )

    except httpx.RequestError as e:
        return False, f"Request error: {str(e)[:100]}", None
    except Exception as e:
        return False, f"Error: {str(e)[:100]}", None


def get_thumbnail_path(image_id: int, filename: str, ext: str) -> Path:
    """Construct thumbnail path from image metadata.

    Thumbnails are stored as: YYYY-MM-DD-{image_id}.{ext}
    But we can also fallback to just checking for any file with the image_id.
    """
    thumbs_dir = Path(settings.STORAGE_PATH) / "thumbs"

    # Try exact filename match first
    if filename:
        thumb_path = thumbs_dir / f"{filename}.jpeg"
        if thumb_path.exists():
            return thumb_path

    # Fallback: search for any file matching pattern *-{image_id}.{ext}
    pattern = f"*-{image_id}.jpeg"
    matches = list(thumbs_dir.glob(pattern))
    if matches:
        return matches[0]

    # Return expected path even if it doesn't exist (for error reporting)
    return thumbs_dir / f"{filename}.{ext}" if filename else thumbs_dir / f"{image_id}.{ext}"


async def iter_image_batches(
    engine, batch_size: int, start_from: int, *, only_missing_hash: bool
):
    """Yield batches of (image_id, filename, ext, iqdb_hash) using keyset pagination."""
    last_id = start_from
    while True:
        async with engine.connect() as conn:
            query = (
                select(Images.image_id, Images.filename, Images.ext, Images.iqdb_hash)
                .where(Images.image_id > last_id)
                .order_by(Images.image_id)
                .limit(batch_size)
            )
            if only_missing_hash:
                query = query.where(Images.iqdb_hash.is_(None))
            result = await conn.execute(query)
            rows = result.fetchall()

        if not rows:
            return
        yield rows
        last_id = rows[-1][0]


async def get_image_count(engine, start_from: int, *, only_missing_hash: bool) -> int:
    """Return the count of images to process."""
    async with engine.connect() as conn:
        query = select(func.count()).select_from(Images).where(Images.image_id > start_from)
        if only_missing_hash:
            query = query.where(Images.iqdb_hash.is_(None))
        result = await conn.execute(query)
        return result.scalar_one()


async def _process_one(
    semaphore: asyncio.Semaphore,
    http_client: httpx.Client,
    session_factory,
    image_id: int,
    filename: str,
    ext: str,
    dry_run: bool,
) -> tuple[int, str]:
    """Process a single image. Returns (image_id, outcome).

    outcome is one of: 'ok' | 'missing' | 'error:<msg>' | 'db_error:<msg>'
    """
    async with semaphore:
        thumb_path = get_thumbnail_path(image_id, filename, ext)
        if not thumb_path.exists():
            return image_id, "missing"

        if dry_run:
            return image_id, "ok"

        # add_image_to_iqdb is sync (uses sync httpx.Client). Run it in a
        # thread so the event loop doesn't block on file IO + iqdb POST.
        success, message, iqdb_hash = await asyncio.to_thread(
            add_image_to_iqdb, http_client, image_id, thumb_path
        )

        if not success:
            return image_id, f"error:{message}"

        try:
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(Images)
                    .where(Images.image_id == image_id)
                    .values(iqdb_hash=iqdb_hash)
                )
        except Exception as e:
            return image_id, f"db_error:{e}"

        return image_id, "ok"


async def populate_iqdb(
    batch_size: int = 100,
    concurrency: int = 50,
    dry_run: bool = False,
    skip_missing: bool = False,
    start_from: int = 0,
    only_missing_hash: bool = False,
) -> None:
    """Main function to populate IQDB with all images."""

    print("=" * 80)
    print("IQDB Population Script")
    print("=" * 80)
    print(f"IQDB Server: {settings.IQDB_HOST}:{settings.IQDB_PORT}")
    print(f"Storage Path: {settings.STORAGE_PATH}")
    print(f"Batch Size: {batch_size}")
    print(f"Concurrency: {concurrency}")
    print(f"Dry Run: {dry_run}")
    print(f"Skip Missing: {skip_missing}")
    print(f"Start From ID: {start_from}")
    print(f"Only Missing Hash: {only_missing_hash}")
    print("=" * 80)

    # Check IQDB availability (skip in dry-run mode)
    if not dry_run:
        print("\nChecking IQDB server availability...")
        if not await check_iqdb_available():
            print("ERROR: IQDB server is not reachable!")
            print(f"   Make sure IQDB is running at {settings.IQDB_HOST}:{settings.IQDB_PORT}")
            sys.exit(1)
        print("IQDB server is available")

    # Create database engine
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Count images to process
    print("\nCounting images to process...")
    total_images = await get_image_count(
        engine, start_from, only_missing_hash=only_missing_hash
    )
    print(f"Found {total_images} images to process")

    if total_images == 0:
        print("No images to process. Exiting.")
        return

    if dry_run:
        print("\nDRY RUN MODE - No changes will be made\n")

    # Process images
    print("\nProcessing images...")
    print("-" * 80)

    success_count = 0
    error_count = 0
    missing_count = 0
    processed = 0
    semaphore = asyncio.Semaphore(concurrency)

    with httpx.Client(timeout=10.0) as http_client:
        async for batch in iter_image_batches(
            engine, batch_size, start_from, only_missing_hash=only_missing_hash
        ):
            outcomes = await asyncio.gather(
                *[
                    _process_one(
                        semaphore,
                        http_client,
                        session_factory,
                        image_id,
                        filename,
                        ext,
                        dry_run,
                    )
                    for image_id, filename, ext, _hash in batch
                ]
            )

            for image_id, outcome in outcomes:
                processed += 1
                if outcome == "ok":
                    success_count += 1
                elif outcome == "missing":
                    missing_count += 1
                    if not skip_missing:
                        print(f"  Image {image_id}: Thumbnail not found")
                        error_count += 1
                    else:
                        print(f"  Image {image_id}: Skipping (thumbnail not found)")
                else:
                    error_count += 1
                    print(f"  Image {image_id}: {outcome}")

            pct = processed / total_images * 100 if total_images > 0 else 0
            print(f"Progress: {processed}/{total_images} ({pct:.1f}%)")

    # Summary
    print("-" * 80)
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total images processed: {processed}")
    print(f"Successfully added:   {success_count}")
    print(f"Errors:              {error_count}")
    print(f"Missing thumbnails:   {missing_count}")

    if dry_run:
        print("\nThis was a DRY RUN - no changes were made")
    else:
        print("\nIQDB population complete!")

    print("=" * 80)

    # Exit with error code if there were errors (but not if just missing thumbnails with skip)
    if error_count > 0 and not skip_missing:
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Populate iqdb-rs with image thumbnails and capture signature hashes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to see what would happen
  uv run python scripts/populate_iqdb.py --dry-run

  # Cutover backfill: only rows missing a hash
  uv run python scripts/populate_iqdb.py --only-missing-hash --skip-missing

  # Resume from a specific image_id
  uv run python scripts/populate_iqdb.py --start-from 500000 --only-missing-hash

  # Process in smaller batches with lower concurrency
  uv run python scripts/populate_iqdb.py --batch-size 50 --concurrency 10
        """,
    )

    parser.add_argument(
        "--batch-size", type=int, default=100, help="Process N images per DB batch (default: 100)"
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Max concurrent iqdb POSTs per batch (default: 50)",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without making changes"
    )

    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip images with missing thumbnails instead of failing",
    )

    parser.add_argument(
        "--start-from",
        type=int,
        default=0,
        metavar="ID",
        help="Start from this image_id (resume helper, default: 0)",
    )

    parser.add_argument(
        "--only-missing-hash",
        action="store_true",
        default=False,
        help="Skip images whose iqdb_hash is already populated (resumable default for cutover backfill)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            populate_iqdb(
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                dry_run=args.dry_run,
                skip_missing=args.skip_missing,
                start_from=args.start_from,
                only_missing_hash=args.only_missing_hash,
            )
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(130)


if __name__ == "__main__":
    main()

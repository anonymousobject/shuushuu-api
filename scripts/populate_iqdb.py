#!/usr/bin/env python3
"""
Populate IQDB with all existing image thumbnails from the database.

This script iterates through all images in the database and adds their
thumbnails to IQDB for similarity search indexing.

Usage:
    uv run python scripts/populate_iqdb.py [--batch-size 100] [--dry-run]

Options:
    --batch-size N    Process N images at a time (default: 100)
    --dry-run         Show what would be done without making changes
    --skip-missing    Skip images with missing thumbnails instead of failing
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.models import Images


async def check_iqdb_available() -> bool:
    """Check if IQDB server is reachable."""
    try:
        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/query"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(iqdb_url.replace("/query", "/health"))
            return response.status_code in (200, 404)  # 404 is OK, means server is up
    except (httpx.RequestError, httpx.TimeoutException):
        return False


def add_image_to_iqdb(image_id: int, thumb_path: Path) -> tuple[bool, str]:
    """Add a single image to IQDB.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        if not thumb_path.exists():
            return False, f"Thumbnail not found: {thumb_path}"

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        with open(thumb_path, "rb") as f:
            files = {"file": (thumb_path.name, f, "image/jpeg")}

            with httpx.Client(timeout=10.0) as client:
                response = client.post(iqdb_url, files=files)

        if response.status_code in (200, 201):
            return True, "OK"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:100]}"

    except httpx.RequestError as e:
        return False, f"Request error: {str(e)[:100]}"
    except Exception as e:
        return False, f"Error: {str(e)[:100]}"


async def get_all_images(engine):
    """Fetch all images from database."""
    async with engine.connect() as conn:
        result = await conn.execute(
            select(Images.image_id, Images.filename, Images.ext)
            .where(Images.status == 1)  # Only active images
            .order_by(Images.image_id)
        )
        return result.fetchall()


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


async def populate_iqdb(
    batch_size: int = 100, dry_run: bool = False, skip_missing: bool = False
) -> None:
    """Main function to populate IQDB with all images."""

    print("=" * 80)
    print("IQDB Population Script")
    print("=" * 80)
    print(f"IQDB Server: {settings.IQDB_HOST}:{settings.IQDB_PORT}")
    print(f"Storage Path: {settings.STORAGE_PATH}")
    print(f"Batch Size: {batch_size}")
    print(f"Dry Run: {dry_run}")
    print(f"Skip Missing: {skip_missing}")
    print("=" * 80)

    # Check IQDB availability
    print("\nChecking IQDB server availability...")
    if not await check_iqdb_available():
        print("❌ ERROR: IQDB server is not reachable!")
        print(f"   Make sure IQDB is running at {settings.IQDB_HOST}:{settings.IQDB_PORT}")
        sys.exit(1)
    print("✓ IQDB server is available")

    # Create database engine
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    # Fetch all images
    print("\nFetching images from database...")
    images = await get_all_images(engine)
    total_images = len(images)
    print(f"✓ Found {total_images} active images")

    if total_images == 0:
        print("No images to process. Exiting.")
        return

    if dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be made\n")

    # Process images
    print("\nProcessing images...")
    print("-" * 80)

    success_count = 0
    error_count = 0
    missing_count = 0

    for idx, (image_id, filename, ext) in enumerate(images, 1):
        thumb_path = get_thumbnail_path(image_id, filename, ext)

        # Progress indicator
        if idx % 10 == 0 or idx == 1:
            print(f"Progress: {idx}/{total_images} ({idx / total_images * 100:.1f}%)")

        # Check if thumbnail exists
        if not thumb_path.exists():
            missing_count += 1
            if skip_missing:
                print(f"  [{idx}] Image {image_id}: Skipping (thumbnail not found)")
                continue
            else:
                print(f"  [{idx}] Image {image_id}: ❌ Thumbnail not found: {thumb_path}")
                error_count += 1
                continue

        # Add to IQDB
        if not dry_run:
            success, message = add_image_to_iqdb(image_id, thumb_path)

            if success:
                success_count += 1
                if idx % 100 == 0:  # Only print every 100th success to reduce noise
                    print(f"  [{idx}] Image {image_id}: ✓ Added to IQDB")
            else:
                error_count += 1
                print(f"  [{idx}] Image {image_id}: ❌ {message}")
        else:
            # Dry run - just count what we would do
            success_count += 1
            if idx % 100 == 0:
                print(f"  [{idx}] Image {image_id}: Would add to IQDB")

        # Small delay to avoid overwhelming IQDB
        # if not dry_run and idx % batch_size == 0:
        #     await asyncio.sleep(0.1)

    # Summary
    print("-" * 80)
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total images processed: {total_images}")
    print(f"✓ Successfully added:   {success_count}")
    print(f"❌ Errors:              {error_count}")
    print(f"⊗ Missing thumbnails:   {missing_count}")

    if dry_run:
        print("\n⚠️  This was a DRY RUN - no changes were made")
    else:
        print("\n✓ IQDB population complete!")

    print("=" * 80)

    # Exit with error code if there were errors (but not if just missing thumbnails with skip)
    if error_count > 0 and not skip_missing:
        sys.exit(1)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Populate IQDB with all existing image thumbnails",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to see what would happen
  uv run python scripts/populate_iqdb.py --dry-run

  # Add all images, skip missing thumbnails
  uv run python scripts/populate_iqdb.py --skip-missing

  # Process in smaller batches
  uv run python scripts/populate_iqdb.py --batch-size 50
        """,
    )

    parser.add_argument(
        "--batch-size", type=int, default=100, help="Process N images at a time (default: 100)"
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without making changes"
    )

    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip images with missing thumbnails instead of failing",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            populate_iqdb(
                batch_size=args.batch_size, dry_run=args.dry_run, skip_missing=args.skip_missing
            )
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Exiting...")
        sys.exit(130)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Convert JPEG thumbnails to WebP using the same pipeline as
app.services.image_processing.create_thumbnail.

Used to recover WebP thumbnails when the JPEG is the only surviving copy.
Source and destination live in the same thumbs/ directory.

Usage:
    uv run python scripts/jpeg_thumbs_to_webp.py [--input-file missing-images] \
        [--thumbs-dir /sakura/shuushuu/images/thumbs] [--overwrite] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter
from PIL.ImageCms import PyCMSError

# Mirror of app.services.image_processing settings
MAX_THUMB_WIDTH = 500
MAX_THUMB_HEIGHT = 500
THUMBNAIL_QUALITY = 75
WEBP_METHOD = 4

_srgb_profile = ImageCms.createProfile("sRGB")


def convert_to_srgb(img: Image.Image) -> Image.Image:
    """Mirror of _convert_to_srgb in app/services/image_processing.py."""
    try:
        icc_profile = img.info.get("icc_profile")
        if icc_profile:
            input_profile = ImageCms.ImageCmsProfile(ImageCms.getOpenProfile(icc_profile))
            if img.mode == "L":
                img = ImageCms.profileToProfile(img, input_profile, _srgb_profile)  # type: ignore[assignment]
            else:
                img = ImageCms.profileToProfile(  # type: ignore[assignment]
                    img, input_profile, _srgb_profile, outputMode="RGB"
                )
    except PyCMSError, OSError, TypeError:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    return img


def jpeg_to_webp(source_path: Path, dest_path: Path) -> tuple[int, int, int, int]:
    """Convert a single JPEG to a WebP thumbnail.

    Returns (orig_w, orig_h, out_w, out_h).
    """
    with Image.open(source_path) as img:
        original_size = img.size

        img = convert_to_srgb(img)  # type: ignore[assignment]

        if img.mode == "RGBA":
            # WebP supports transparency, preserve alpha
            pass
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")  # type: ignore[assignment]

        img.thumbnail(
            (MAX_THUMB_WIDTH, MAX_THUMB_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=50, threshold=3))  # type: ignore[assignment]

        img.save(
            dest_path,
            format="WEBP",
            quality=THUMBNAIL_QUALITY,
            method=WEBP_METHOD,
        )

    return original_size[0], original_size[1], img.size[0], img.size[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=Path("missing-images"),
        help="Newline-delimited list of JPEG filenames (default: ./missing-images)",
    )
    parser.add_argument(
        "--thumbs-dir",
        type=Path,
        default=Path("/sakura/shuushuu/images/thumbs"),
        help="Directory containing the JPEG sources (and where WebPs are written)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Recreate WebP even if it already exists",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.input_file.exists():
        print(f"ERROR: input file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    if not args.thumbs_dir.is_dir():
        print(f"ERROR: thumbs dir not found: {args.thumbs_dir}", file=sys.stderr)
        sys.exit(1)

    filenames = [line.strip() for line in args.input_file.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(filenames)} entries from {args.input_file}")
    print(f"Thumbs dir: {args.thumbs_dir}")
    print(f"Overwrite: {args.overwrite} | Dry run: {args.dry_run}")
    print("-" * 80)

    created = 0
    skipped = 0
    errors = 0
    missing_source = 0

    for fname in filenames:
        src = args.thumbs_dir / fname
        if src.suffix.lower() not in (".jpg", ".jpeg"):
            print(f"  SKIP (not jpeg): {fname}", file=sys.stderr)
            skipped += 1
            continue
        if not src.exists():
            print(f"  MISSING SOURCE: {src}", file=sys.stderr)
            missing_source += 1
            errors += 1
            continue

        dst = src.with_suffix(".webp")
        if dst.exists() and not args.overwrite:
            skipped += 1
            continue

        if args.dry_run:
            print(f"  WOULD CREATE: {dst.name} (from {src.name})")
            created += 1
            continue

        try:
            ow, oh, dw, dh = jpeg_to_webp(src, dst)
            size = dst.stat().st_size
            print(f"  OK {dst.name} ({ow}x{oh} -> {dw}x{dh}, {size:,} bytes)")
            created += 1
        except Exception as e:
            print(f"  ERROR {fname}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
            # Clean up partial output if any
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass

    print("-" * 80)
    print(f"Created:  {created}")
    print(f"Skipped:  {skipped}")
    print(f"Errors:   {errors}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

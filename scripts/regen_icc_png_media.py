#!/usr/bin/env python3
"""
Rebuild thumbs and medium/large variants for RGBA PNGs that carry an ICC profile.

Until PR #404, app.services.image_processing._convert_to_srgb failed to open
the embedded profile and fell back to flattening RGBA -> RGB, so every derived
file for such a source lost its transparency (whatever colour sat under the
transparent pixels became the "background"). Untagged PNGs were unaffected.
This script finds the affected sources and regenerates their derived files
with the fixed pipeline, then re-uploads them to R2 (with a CDN purge) for
rows that are already synced.

Two phases so the candidate list can be reviewed before touching prod media:

    # 1. Scan every PNG row's fullsize header (mode + ICC presence only; no
    #    pixel decode) and write the affected image ids, one per line.
    uv run python scripts/regen_icc_png_media.py scan [--min-id N] [--output icc-png-candidates]

    # 2. Regenerate. Appends each finished id to <candidates>.done so an
    #    interrupted run resumes where it left off.
    uv run python scripts/regen_icc_png_media.py fix [--candidates icc-png-candidates] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image
from sqlalchemy import select, update
from sqlmodel import col

from app.config import settings
from app.core.database import get_async_session
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.services.image_processing import _create_variant, create_thumbnail
from scripts.r2_sync import force_reupload_image

DERIVED_VARIANTS = {"thumbs", "medium", "large"}


def is_affected_png(path: Path) -> bool:
    """True when the file is RGBA and carries an ICC profile.

    Reads the header only; Image.open is lazy and does not decode pixels, so
    Pillow's decompression-bomb pixel ceiling is lifted for the duration of
    the check (prod has legitimate PNGs past the default 178M-pixel limit).
    """
    pixel_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as img:
            return img.mode == "RGBA" and bool(img.info.get("icc_profile"))
    finally:
        Image.MAX_IMAGE_PIXELS = pixel_limit


@dataclass
class ScanResult:
    candidates: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    unreadable: list[tuple[int, str]] = field(default_factory=list)
    scanned: int = 0


def scan(rows: Iterable[tuple[int, str, str]], storage_path: str) -> ScanResult:
    """Check each (image_id, filename, ext) row's fullsize file."""
    result = ScanResult()
    last_report = time.time()
    for image_id, filename, ext in rows:
        result.scanned += 1
        source = Path(storage_path) / "fullsize" / f"{filename}.{ext}"
        if not source.exists():
            result.missing.append(image_id)
            continue
        try:
            affected = is_affected_png(source)
        except Exception as exc:
            result.unreadable.append((image_id, f"{type(exc).__name__}: {exc}"))
            continue
        if affected:
            result.candidates.append(image_id)
        if time.time() - last_report >= 5:
            print(
                f"  scanned {result.scanned:,} | affected {len(result.candidates):,} "
                f"| missing {len(result.missing):,} | unreadable {len(result.unreadable):,}",
                flush=True,
            )
            last_report = time.time()
    return result


async def fetch_png_rows(*, min_id: int | None) -> list[tuple[int, str, str]]:
    """All PNG image rows as (image_id, filename, ext), ascending by id."""
    query = select(Images.image_id, Images.filename, Images.ext).where(  # type: ignore[call-overload]
        col(Images.ext) == "png"
    )
    if min_id is not None:
        query = query.where(col(Images.image_id) >= min_id)
    query = query.order_by(col(Images.image_id))
    async with get_async_session() as db:
        result = await db.execute(query)
        return [(row[0], row[1], row[2]) for row in result.all()]


def pending_ids(candidates_file: Path, done_file: Path) -> list[int]:
    """Candidate ids not yet recorded in the done file."""
    done: set[int] = set()
    if done_file.exists():
        done = {int(line) for line in done_file.read_text().split() if line.strip()}
    return [
        int(line)
        for line in candidates_file.read_text().split()
        if line.strip() and int(line) not in done
    ]


@dataclass
class FixOutcome:
    image_id: int
    ok: bool
    message: str


async def fix_image(image_id: int, *, dry_run: bool) -> FixOutcome:
    """Regenerate thumb + medium/large for one image and re-sync R2 if needed.

    Mirrors what create_thumbnail_job and create_variant_job do on upload,
    including the medium/large status flip (READY when created, NONE when
    below threshold or not smaller than the original).
    """
    async with get_async_session() as db:
        result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
        image = result.scalar_one_or_none()
        if image is None:
            return FixOutcome(image_id, False, "not found")

        source = Path(settings.STORAGE_PATH) / "fullsize" / f"{image.filename}.{image.ext}"
        if not source.exists():
            return FixOutcome(image_id, False, f"source not found: {source}")

        synced = image.r2_location != R2Location.NONE
        if dry_run:
            r2_note = " + R2 reupload" if settings.R2_ENABLED and synced else ""
            return FixOutcome(image_id, True, f"would regenerate{r2_note}")

        await asyncio.to_thread(
            create_thumbnail, source, image_id, image.ext, settings.STORAGE_PATH
        )
        statuses: dict[str, int] = {}
        for variant_type, threshold in (
            ("medium", settings.MEDIUM_EDGE),
            ("large", settings.LARGE_EDGE),
        ):
            created = await asyncio.to_thread(
                _create_variant,
                source_path=source,
                image_id=image_id,
                ext=image.ext,
                storage_path=settings.STORAGE_PATH,
                width=image.width,
                height=image.height,
                size_threshold=threshold,
                variant_type=variant_type,
            )
            statuses[variant_type] = VariantStatus.READY if created is True else VariantStatus.NONE
        await db.execute(
            update(Images).where(Images.image_id == image_id).values(**statuses)  # type: ignore[arg-type]
        )
        await db.commit()

    if settings.R2_ENABLED and synced:
        await force_reupload_image(image_id=image_id, dry_run=dry_run, only=DERIVED_VARIANTS)

    return FixOutcome(
        image_id,
        True,
        f"regenerated (medium={statuses['medium']}, large={statuses['large']})",
    )


async def cmd_scan(*, min_id: int | None, output: Path) -> int:
    print(f"Storage path: {settings.STORAGE_PATH}")
    print("Fetching PNG rows...")
    rows = await fetch_png_rows(min_id=min_id)
    print(f"Scanning {len(rows):,} PNG sources...")
    result = scan(rows, settings.STORAGE_PATH)
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
    ids = pending_ids(candidates, done_file)
    if limit is not None:
        ids = ids[:limit]
    print(f"Storage path: {settings.STORAGE_PATH} | R2: {settings.R2_ENABLED} | Dry run: {dry_run}")
    print(f"Pending: {len(ids):,} (done file: {done_file})")
    print("-" * 80)

    fixed = errors = 0
    with done_file.open("a") as done:
        for image_id in ids:
            try:
                outcome = await fix_image(image_id, dry_run=dry_run)
            except Exception as exc:
                outcome = FixOutcome(image_id, False, f"{type(exc).__name__}: {exc}")
            if outcome.ok:
                fixed += 1
                print(f"  OK {image_id}: {outcome.message}", flush=True)
                if not dry_run:
                    done.write(f"{image_id}\n")
                    done.flush()
            else:
                errors += 1
                print(f"  ERROR {image_id}: {outcome.message}", file=sys.stderr, flush=True)

    print("-" * 80)
    print(f"Fixed:   {fixed:,}")
    print(f"Errors:  {errors:,}")
    return 1 if errors else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="find affected sources, write candidate ids")
    scan_parser.add_argument(
        "--min-id", type=int, default=None, help="only rows with image_id >= N"
    )
    scan_parser.add_argument("--output", type=Path, default=Path("icc-png-candidates"))

    fix_parser = sub.add_parser("fix", help="regenerate derived files for candidate ids")
    fix_parser.add_argument("--candidates", type=Path, default=Path("icc-png-candidates"))
    fix_parser.add_argument("--limit", type=int, default=None, help="stop after N images")
    fix_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "scan":
        return asyncio.run(cmd_scan(min_id=args.min_id, output=args.output))
    if not args.candidates.exists():
        print(f"ERROR: candidates file not found: {args.candidates}", file=sys.stderr)
        return 1
    return asyncio.run(cmd_fix(candidates=args.candidates, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())

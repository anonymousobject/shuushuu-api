#!/usr/bin/env python3
"""
Analyze co-occurrence patterns between character and source tags.

This script identifies likely character-source relationships by analyzing
which source tags frequently co-occur with character tags on the same images.

Usage:
    # Analyze only (preview mode)
    uv run python scripts/analyze_character_sources.py [--threshold 0.8] [--min-images 5]

    # Export to CSV for review
    uv run python scripts/analyze_character_sources.py --output results.csv

    # Create links in database
    uv run python scripts/analyze_character_sources.py --create-links [--user-id 1]

    # Report conflated-candidate characters (>= 2 dominant sources; report-only)
    uv run python scripts/analyze_character_sources.py --conflated [--min-usage 20] [--min-share 0.05]
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import TagType, settings
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from app.models.tag_link import TagLinks


async def analyze_character_sources(
    threshold: float = 0.8,
    min_images: int = 5,
    output_file: str | None = None,
) -> list[dict[str, Any]]:
    """
    Analyze co-occurrence patterns between character and source tags.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    results = []

    async with async_session() as db:
        # Get all character tags with usage >= min_images
        char_result = await db.execute(
            select(Tags.tag_id, Tags.title)  # type: ignore[call-overload]
            .where(Tags.type == TagType.CHARACTER)
            .where(Tags.usage_count >= min_images)
            .order_by(Tags.title)
        )
        character_tags = char_result.all()

        print(f"Found {len(character_tags)} character tags with >= {min_images} images")

        for char_id, char_title in character_tags:
            # Get count of images with this character
            count_result = await db.execute(
                select(func.count(TagLinks.image_id)).where(  # type: ignore[arg-type]
                    TagLinks.tag_id == char_id
                )
            )
            total_images = count_result.scalar() or 0

            if total_images < min_images:
                continue

            # Use subquery for image_ids instead of loading into Python memory
            image_subquery = (
                select(TagLinks.image_id)  # type: ignore[call-overload]
                .where(TagLinks.tag_id == char_id)
                .subquery()
            )

            # Count source tags that co-occur
            source_counts_result = await db.execute(
                select(Tags.tag_id, Tags.title, func.count(TagLinks.image_id).label("count"))  # type: ignore[call-overload, arg-type]
                .join(TagLinks, Tags.tag_id == TagLinks.tag_id)
                .where(Tags.type == TagType.SOURCE)
                .where(TagLinks.image_id.in_(select(image_subquery)))  # type: ignore[attr-defined]
                .group_by(Tags.tag_id, Tags.title)
                .order_by(func.count(TagLinks.image_id).desc())  # type: ignore[arg-type]
            )

            for source_id, source_title, count in source_counts_result.all():
                percentage = count / total_images
                if percentage >= threshold:
                    results.append(
                        {
                            "character_tag_id": char_id,
                            "character_title": char_title,
                            "source_tag_id": source_id,
                            "source_title": source_title,
                            "co_occurrence_count": count,
                            "total_character_images": total_images,
                            "percentage": round(percentage * 100, 1),
                        }
                    )

    await engine.dispose()

    # Sort by percentage descending, then by character name
    results.sort(key=lambda x: (-x["percentage"], x["character_title"]))

    # Print results
    print(f"\nFound {len(results)} candidate links (>= {threshold * 100}% co-occurrence):\n")
    for r in results[:50]:
        print(
            f"  {r['character_title']} -> {r['source_title']}: "
            f"{r['co_occurrence_count']}/{r['total_character_images']} ({r['percentage']}%)"
        )
    if len(results) > 50:
        print(f"  ... and {len(results) - 50} more")

    # Write to CSV if requested
    if output_file:
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys() if results else [])
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {output_file}")

    return results


async def create_links_from_results(
    results: list[dict[str, Any]],
    user_id: int | None = None,
    batch_size: int = 100,
) -> tuple[int, int]:
    """
    Create character-source links from analysis results.

    Uses batch inserts for performance. If a batch fails due to duplicates,
    falls back to individual inserts for that batch.

    Returns (created_count, skipped_count).
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    created = 0
    skipped = 0
    total = len(results)

    async with async_session() as db:
        for batch_start in range(0, total, batch_size):
            batch = results[batch_start : batch_start + batch_size]

            # Try batch insert first
            for r in batch:
                link = CharacterSourceLinks(
                    character_tag_id=r["character_tag_id"],
                    source_tag_id=r["source_tag_id"],
                    created_by_user_id=user_id,
                )
                db.add(link)

            try:
                await db.commit()
                created += len(batch)
                print(f"  Batch {batch_start // batch_size + 1}: created {len(batch)} links")
            except IntegrityError:
                # Batch had duplicates - fall back to individual inserts
                await db.rollback()
                batch_created = 0
                batch_skipped = 0

                for r in batch:
                    link = CharacterSourceLinks(
                        character_tag_id=r["character_tag_id"],
                        source_tag_id=r["source_tag_id"],
                        created_by_user_id=user_id,
                    )
                    db.add(link)
                    try:
                        await db.commit()
                        batch_created += 1
                    except IntegrityError:
                        await db.rollback()
                        batch_skipped += 1

                created += batch_created
                skipped += batch_skipped
                print(
                    f"  Batch {batch_start // batch_size + 1}: "
                    f"created {batch_created}, skipped {batch_skipped} duplicates"
                )

    await engine.dispose()
    return created, skipped


async def find_conflated_characters(
    db: AsyncSession,
    *,
    min_usage: int = 20,
    min_images: int = 5,
    min_share: float = 0.05,
) -> list[dict[str, Any]]:
    """
    Report character tags whose images split across >= 2 dominant sources.

    A source qualifies when it co-occurs on >= min_images of the character's
    images AND covers >= min_share of the character's source-tagged images.
    Alias source tags roll up into their canonical tag. Report-only: a human
    decides which combinations get links (true conflation and legitimate
    multi-franchise appearances both warrant them).
    """
    char_rows = (
        await db.execute(
            select(Tags.tag_id, Tags.title)  # type: ignore[call-overload]
            .where(Tags.type == TagType.CHARACTER)
            .where(Tags.usage_count >= min_usage)
            .order_by(Tags.tag_id)
        )
    ).all()

    link_count_rows = (
        await db.execute(
            select(  # type: ignore[call-overload]
                CharacterSourceLinks.character_tag_id, func.count()
            ).group_by(CharacterSourceLinks.character_tag_id)
        )
    ).all()
    link_counts: dict[int, int] = dict(link_count_rows)  # type: ignore[arg-type]

    results: list[dict[str, Any]] = []
    for char_id, char_title in char_rows:
        image_subquery = (
            select(TagLinks.image_id)  # type: ignore[call-overload]
            .where(TagLinks.tag_id == char_id)
            .subquery()
        )

        total_images = (
            await db.execute(
                select(func.count(TagLinks.image_id)).where(  # type: ignore[arg-type]
                    TagLinks.tag_id == char_id
                )
            )
        ).scalar() or 0
        # usage_count is denormalized and can drift; re-check against live links
        if total_images < min_usage:
            continue

        canonical_id = func.coalesce(Tags.alias_of, Tags.tag_id).label("source_tag_id")
        source_rows = (
            await db.execute(
                select(canonical_id, func.count(func.distinct(TagLinks.image_id)).label("count"))
                .join(TagLinks, Tags.tag_id == TagLinks.tag_id)  # type: ignore[arg-type]
                .where(Tags.type == TagType.SOURCE)  # type: ignore[arg-type]
                .where(TagLinks.image_id.in_(select(image_subquery)))  # type: ignore[attr-defined]
                .group_by(canonical_id)
            )
        ).all()

        source_tagged = (
            await db.execute(
                select(func.count(func.distinct(TagLinks.image_id)))
                .join(Tags, Tags.tag_id == TagLinks.tag_id)  # type: ignore[arg-type]
                .where(Tags.type == TagType.SOURCE)  # type: ignore[arg-type]
                .where(TagLinks.image_id.in_(select(image_subquery)))  # type: ignore[attr-defined]
            )
        ).scalar() or 0
        if source_tagged == 0:
            continue

        qualifying = [
            (source_id, count)
            for source_id, count in source_rows
            if count >= min_images and count / source_tagged >= min_share
        ]
        if len(qualifying) < 2:
            continue
        qualifying.sort(key=lambda pair: -pair[1])

        results.append(
            {
                "character_tag_id": char_id,
                "character_title": char_title,
                "total_character_images": total_images,
                "source_tagged_images": source_tagged,
                "linked_count": link_counts.get(char_id, 0),
                "sources": [
                    {
                        "source_tag_id": source_id,
                        "count": count,
                        "share": round(count / source_tagged, 3),
                    }
                    for source_id, count in qualifying
                ],
            }
        )

    source_ids = {s["source_tag_id"] for r in results for s in r["sources"]}
    if source_ids:
        title_rows = (
            await db.execute(
                select(Tags.tag_id, Tags.title).where(Tags.tag_id.in_(source_ids))  # type: ignore[call-overload, union-attr]
            )
        ).all()
        titles: dict[int, str | None] = dict(title_rows)  # type: ignore[arg-type]
        for r in results:
            for s in r["sources"]:
                s["source_title"] = titles.get(s["source_tag_id"])

    results.sort(key=lambda r: -r["total_character_images"])
    return results


async def run_conflated(
    min_usage: int, min_images: int, min_share: float, output_file: str | None
) -> list[dict[str, Any]]:
    """CLI wrapper: open a session, run the report, print, optionally CSV."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        results = await find_conflated_characters(
            db, min_usage=min_usage, min_images=min_images, min_share=min_share
        )
    await engine.dispose()

    print(
        f"\n{len(results)} conflated-candidate character tags "
        f"(>= 2 sources with >= {min_images} images and >= {min_share:.0%} share):\n"
    )
    for r in results:
        sources = ", ".join(f"{s['source_title']} ({s['count']})" for s in r["sources"])
        print(
            f"  #{r['character_tag_id']} {r['character_title']} — "
            f"{r['total_character_images']} images, links: {r['linked_count']} — {sources}"
        )

    if output_file:
        char_fields = [
            "character_tag_id",
            "character_title",
            "total_character_images",
            "source_tagged_images",
            "linked_count",
        ]
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=char_fields + ["source_tag_id", "source_title", "count", "share"]
            )
            writer.writeheader()
            for r in results:
                for s in r["sources"]:
                    writer.writerow({**{k: r[k] for k in char_fields}, **s})
        print(f"\nResults written to {output_file}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze character-source co-occurrence")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Minimum co-occurrence percentage (0.0-1.0, default: 0.8)",
    )
    parser.add_argument(
        "--min-images",
        type=int,
        default=5,
        help="Dominant mode: minimum images for a character to be considered; --conflated mode: minimum per-source image count (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path",
    )
    parser.add_argument(
        "--create-links",
        action="store_true",
        help="Create character-source links in database (default: analyze only)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="User ID to record as link creator (optional, used with --create-links)",
    )
    parser.add_argument(
        "--conflated",
        action="store_true",
        help="Report characters whose images split across >= 2 dominant sources (report-only)",
    )
    parser.add_argument(
        "--min-usage",
        type=int,
        default=20,
        help="(--conflated) minimum character tag usage (default: 20)",
    )
    parser.add_argument(
        "--min-share",
        type=float,
        default=0.05,
        help="(--conflated) minimum share of source-tagged images per source (default: 0.05)",
    )
    args = parser.parse_args()

    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0.0 and 1.0")
    if args.min_images < 1:
        parser.error("--min-images must be at least 1")
    if args.user_id is not None and args.user_id < 1:
        parser.error("--user-id must be a positive integer")
    if args.conflated and args.create_links:
        parser.error("--conflated is report-only and cannot be combined with --create-links")
    if not 0.0 <= args.min_share <= 1.0:
        parser.error("--min-share must be between 0.0 and 1.0")
    if args.min_usage < 1:
        parser.error("--min-usage must be at least 1")

    try:
        if args.conflated:
            asyncio.run(run_conflated(args.min_usage, args.min_images, args.min_share, args.output))
            return

        results = asyncio.run(
            analyze_character_sources(
                threshold=args.threshold,
                min_images=args.min_images,
                output_file=args.output,
            )
        )

        if args.create_links:
            if not results:
                print("\nNo links to create.")
            else:
                print(f"\nCreating {len(results)} character-source links...")
                created, skipped = asyncio.run(
                    create_links_from_results(results, user_id=args.user_id)
                )
                print(f"\nDone: {created} created, {skipped} already existed")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

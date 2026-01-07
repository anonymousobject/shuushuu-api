#!/usr/bin/env python3
"""
Analyze co-occurrence patterns between character and source tags.

This script identifies likely character-source relationships by analyzing
which source tags frequently co-occur with character tags on the same images.

Usage:
    uv run python scripts/analyze_character_sources.py [--threshold 0.8] [--min-images 5] [--output results.csv]
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import TagType, settings
from app.models.tag import Tags
from app.models.tag_link import TagLinks


async def analyze_character_sources(
    threshold: float = 0.8,
    min_images: int = 5,
    output_file: str | None = None,
) -> list[dict]:
    """
    Analyze co-occurrence patterns between character and source tags.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    results = []

    async with async_session() as db:
        # Get all character tags with usage >= min_images
        char_result = await db.execute(
            select(Tags.tag_id, Tags.title)
            .where(Tags.type == TagType.CHARACTER)
            .where(Tags.usage_count >= min_images)
            .order_by(Tags.title)
        )
        character_tags = char_result.all()

        print(f"Found {len(character_tags)} character tags with >= {min_images} images")

        for char_id, char_title in character_tags:
            # Get count of images with this character
            count_result = await db.execute(
                select(func.count(TagLinks.image_id))
                .where(TagLinks.tag_id == char_id)
            )
            total_images = count_result.scalar() or 0

            if total_images < min_images:
                continue

            # Use subquery for image_ids instead of loading into Python memory
            image_subquery = select(TagLinks.image_id).where(TagLinks.tag_id == char_id).subquery()

            # Count source tags that co-occur
            source_counts_result = await db.execute(
                select(Tags.tag_id, Tags.title, func.count(TagLinks.image_id).label("count"))
                .join(TagLinks, Tags.tag_id == TagLinks.tag_id)
                .where(Tags.type == TagType.SOURCE)
                .where(TagLinks.image_id.in_(select(image_subquery)))
                .group_by(Tags.tag_id, Tags.title)
                .order_by(func.count(TagLinks.image_id).desc())
            )

            for source_id, source_title, count in source_counts_result.all():
                percentage = count / total_images
                if percentage >= threshold:
                    results.append({
                        "character_tag_id": char_id,
                        "character_title": char_title,
                        "source_tag_id": source_id,
                        "source_title": source_title,
                        "co_occurrence_count": count,
                        "total_character_images": total_images,
                        "percentage": round(percentage * 100, 1),
                    })

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


def main():
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
        help="Minimum images for a character to be considered (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path",
    )
    args = parser.parse_args()

    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0.0 and 1.0")
    if args.min_images < 1:
        parser.error("--min-images must be at least 1")

    try:
        asyncio.run(
            analyze_character_sources(
                threshold=args.threshold,
                min_images=args.min_images,
                output_file=args.output,
            )
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

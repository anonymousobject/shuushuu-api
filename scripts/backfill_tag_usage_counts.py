#!/usr/bin/env python3
"""
Backfill tag usage_count from tag_links table.

This script calculates the initial usage_count for all tags based on how many images
are tagged with each tag. This is needed because the usage_count column and triggers
were added after the database already had thousands of tags.

The triggers will maintain these counts going forward automatically.
"""

import asyncio
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import Tags, TagLinks
from app.config import settings


async def backfill_usage_counts() -> None:
    """Calculate usage_count for all tags based on tag_links."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, future=True
    )

    async with async_session() as db:
        print("Backfilling tag usage_count from tag_links...")

        # Get count of how many images have each tag
        # Count DISTINCT image_ids per tag_id to handle if the same image has the same tag multiple times
        stmt = (
            select(TagLinks.tag_id, func.count(func.distinct(TagLinks.image_id)).label("count"))
            .group_by(TagLinks.tag_id)
        )
        result = await db.execute(stmt)
        tag_counts = result.all()

        print(f"Found {len(tag_counts)} tags with usage data")

        # Bulk update all tags' usage_count in one statement
        await db.execute(
            text("""
                UPDATE tags
                SET usage_count = COALESCE((
                    SELECT COUNT(DISTINCT tag_links.image_id)
                    FROM tag_links
                    WHERE tag_links.tag_id = tags.tag_id
                ), 0)
            """)
        )

        await db.commit()
        print("Updated all tags with usage counts")

        # Show statistics
        stats_result = await db.execute(
            select(
                func.count(Tags.tag_id).label("total_tags"),
                func.sum(Tags.usage_count).label("total_usage"),
                func.avg(Tags.usage_count).label("avg_usage"),
                func.max(Tags.usage_count).label("max_usage"),
            )
        )
        stats = stats_result.first()
        print(f"\nStatistics:")
        print(f"  Total tags: {stats[0]}")
        print(f"  Total usage count: {stats[1]}")
        print(f"  Average usage per tag: {stats[2]:.2f}" if stats and stats[2] is not None else "  Average usage per tag: 0.00")
        print(f"  Most used tag has: {stats[3]} images")


if __name__ == "__main__":
    asyncio.run(backfill_usage_counts())

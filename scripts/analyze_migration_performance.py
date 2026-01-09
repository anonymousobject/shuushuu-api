"""
Performance analysis for quoted comments migration.
Tests with a small subset to identify bottlenecks.
"""

import asyncio
import time
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import Comments
from app.core.logging import get_logger

logger = get_logger(__name__)


async def analyze_performance():
    """Analyze performance bottlenecks with a small dataset."""
    async with get_async_session() as db:
        # First, get total comment count
        count_result = await db.execute(select(func.count(Comments.post_id)))
        total = count_result.scalar()
        print(f"\n📊 Total comments in database: {total:,}")

        # Test 1: Loading ALL comments into memory
        print("\n" + "=" * 70)
        print("TEST 1: Loading ALL comments into memory")
        print("=" * 70)

        start = time.time()
        result = await db.execute(select(Comments))
        all_comments = result.scalars().all()
        elapsed = time.time() - start
        print(f"Time to load {len(all_comments):,} comments: {elapsed:.2f}s")
        print(f"Memory impact: ~{len(all_comments) * 2_000 / 1_024 / 1_024:.1f}MB (estimated)")

        # Count quoted comments
        quoted_count = sum(1 for c in all_comments if '[quote=' in c.post_text or c.post_text.strip().startswith('>'))
        print(f"Comments with quotes: {quoted_count:,} ({quoted_count/len(all_comments)*100:.1f}%)")

        # Test 2: Query-based approach for quoted comments
        print("\n" + "=" * 70)
        print("TEST 2: Query-based detection of quoted comments")
        print("=" * 70)

        start = time.time()
        result = await db.execute(
            select(Comments).where(
                (Comments.post_text.like('%[quote=%')) |
                (Comments.post_text.like('>%'))
            )
        )
        quoted_via_query = result.scalars().all()
        elapsed = time.time() - start
        print(f"Time to find {len(quoted_via_query):,} quoted comments: {elapsed:.2f}s")
        print(f"Advantage over loading all: {(quoted_count / len(all_comments) * 100):.1f}% fewer comments in memory")

        # Test 3: Individual lookups (current approach)
        print("\n" + "=" * 70)
        print("TEST 3: Individual lookups for 100 sample quoted comments")
        print("=" * 70)

        sample_comments = quoted_via_query[:100]
        print(f"Testing with {len(sample_comments)} sample comments...")

        start = time.time()
        matches = 0
        for i, comment in enumerate(sample_comments):
            # Simulate find_parent_comment - individual query per comment
            result = await db.execute(
                select(Comments).where(
                    (Comments.image_id == comment.image_id) &
                    (Comments.post_id != comment.post_id) &
                    (Comments.post_text == comment.post_text[:50])  # Simplified for timing
                )
            )
            match = result.scalars().first()
            if match:
                matches += 1
            if (i + 1) % 20 == 0:
                print(f"  - Processed {i+1} comments...")

        elapsed = time.time() - start
        print(f"Time for 100 individual lookups: {elapsed:.2f}s ({elapsed/100:.3f}s per lookup)")
        print(f"Matches found: {matches}")

        # Extrapolate for full dataset
        quoted_in_full = quoted_count
        estimated_time = (elapsed / 100) * quoted_in_full
        print(f"\n⏱️  ESTIMATED TIME for full {quoted_in_full:,} quoted comments:")
        print(f"   {estimated_time:.0f}s ({estimated_time/60:.1f} minutes)")

        # Test 4: Batch lookup approach (proposed optimization)
        print("\n" + "=" * 70)
        print("TEST 4: Batch lookup approach (optimized)")
        print("=" * 70)

        # For each image, get all comments once, then search in memory
        start = time.time()

        # Get all image IDs from quoted comments
        image_ids = list(set(c.image_id for c in sample_comments))
        print(f"Processing {len(sample_comments)} comments from {len(image_ids)} images...")

        # Get all comments for these images
        result = await db.execute(
            select(Comments).where(Comments.image_id.in_(image_ids))
        )
        comments_by_image_dict = {}
        for comment in result.scalars().all():
            if comment.image_id not in comments_by_image_dict:
                comments_by_image_dict[comment.image_id] = []
            comments_by_image_dict[comment.image_id].append(comment)

        # Now search in memory
        batch_matches = 0
        for comment in sample_comments:
            if comment.image_id in comments_by_image_dict:
                for candidate in comments_by_image_dict[comment.image_id]:
                    if candidate.post_id != comment.post_id and candidate.post_text == comment.post_text[:50]:
                        batch_matches += 1
                        break

        elapsed = time.time() - start
        print(f"Time for batch lookup: {elapsed:.2f}s")
        print(f"Matches found: {batch_matches}")
        print(f"Speedup vs individual queries: {(elapsed/100)/(elapsed/len(sample_comments)):.1f}x")

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY & RECOMMENDATIONS")
        print("=" * 70)
        print(f"""
Current approach (load all + individual lookups):
  - Loads {total:,} comments into memory
  - Makes {quoted_in_full:,} individual database queries
  - Estimated time: ~{estimated_time/60:.1f} minutes
  - BOTTLENECK: Individual queries for each comment

Optimized approach (batch by image):
  - Loads only quoted comments (~{quoted_in_full:,})
  - Groups by image, fetches once per image group
  - Searches in memory for matches
  - Should be 10-100x faster

Recommended next step:
  - Refactor to batch-lookup approach
  - Use bulk UPDATE instead of individual updates
  - Should complete in 1-5 minutes instead of hours
""")


if __name__ == "__main__":
    asyncio.run(analyze_performance())

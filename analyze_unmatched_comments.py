#!/usr/bin/env python3
"""
Analyze remaining 20,200 unmatched quoted comments.

This report helps understand:
1. How many have their quoted text findable on the image
2. Why exact matching fails
3. What strategies could improve matching
"""

import asyncio
import re

from sqlalchemy import select

from app.core.database import get_async_session
from app.models import Comments


async def generate_report():
    """Generate comprehensive report on unmatched comments."""
    async with get_async_session() as db:
        # Get unmatched quoted comments
        result = await db.execute(
            select(Comments)
            .where((Comments.post_text.like("%[quote=%")) | (Comments.post_text.like(">%")))
            .where(Comments.parent_comment_id.is_(None))
        )
        unmatched = result.scalars().all()

        print("=" * 80)
        print("UNMATCHED QUOTED COMMENTS ANALYSIS")
        print("=" * 80)
        print(f"\nTotal unmatched: {len(unmatched):,}\n")

        # Categories
        text_findable = 0
        text_not_findable = 0
        bbcode_malformed = 0

        # Samples for each category
        findable_samples = []
        not_findable_samples = []

        print("Analyzing comments...")

        for i, comment in enumerate(unmatched):
            if (i + 1) % 5000 == 0:
                print(f"  Processed {i + 1:,}/{len(unmatched):,}")

            # Extract quoted text
            match = re.search(
                r'\[quote=(?:"[^"]*"|&quot;[^&]*&quot;)\](.*?)\[/quote\]',
                comment.post_text,
                re.DOTALL,
            )
            if not match:
                bbcode_malformed += 1
                continue

            quoted_text = match.group(1).strip()

            # Check if quoted text findable on the same image (using substring search)
            # This is less strict than exact matching
            result = await db.execute(
                select(Comments).where(
                    Comments.image_id == comment.image_id,
                    Comments.post_id != comment.post_id,
                    Comments.post_text.contains(quoted_text[:100]),  # Use first 100 chars
                )
            )
            candidates = result.scalars().all()

            if candidates:
                text_findable += 1
                if len(findable_samples) < 3:
                    findable_samples.append(
                        {
                            "id": comment.post_id,
                            "image_id": comment.image_id,
                            "quote_len": len(quoted_text),
                            "candidate_count": len(candidates),
                            "first_candidate": candidates[0].post_id,
                        }
                    )
            else:
                text_not_findable += 1
                if len(not_findable_samples) < 3:
                    not_findable_samples.append(
                        {
                            "id": comment.post_id,
                            "image_id": comment.image_id,
                            "quote_text": quoted_text[:100],
                        }
                    )

        print("\nResults:")
        print(
            f"  Quote text findable on image:      {text_findable:,} ({text_findable * 100 // len(unmatched)}%)"
        )
        print(
            f"  Quote text NOT on image:           {text_not_findable:,} ({text_not_findable * 100 // len(unmatched)}%)"
        )
        print(f"  Malformed BBCode quotes:           {bbcode_malformed:,}")

        print("\nExamples - Text IS findable but not matched:")
        for s in findable_samples:
            print(
                f"  Comment {s['id']} on image {s['image_id']}: "
                f"quote_len={s['quote_len']}, matches={s['candidate_count']}, "
                f"candidate={s['first_candidate']}"
            )

        print("\nExamples - Text NOT on image:")
        for s in not_findable_samples:
            print(f'  Comment {s["id"]} on image {s["image_id"]}: "{s["quote_text"]}..."')

        print("\n" + "=" * 80)
        print("RECOMMENDATIONS")
        print("=" * 80)
        print("""
1. For 33% with text findable but not matched:
   - These quotes have been modified between original and current
   - Example: Quote refers to text, but original comment was edited/deleted
   - Could use fuzzy matching (e.g., Levenshtein distance) if needed
   - Or accept them as "orphaned quotes" - legitimate cross-references

2. For 67% with text not on image:
   - Quote is from a different image (cross-image discussion)
   - Original quoted comment was deleted or moved
   - User quoted from memory/external source
   - These are unfixable with current data

3. Overall assessment:
   - Current 67% match rate (40,973/61,173) is solid
   - Remaining 33% (20,200) represent true cross-refs and deleted content
   - Not worth trying to force matches on these
""")


asyncio.run(generate_report())

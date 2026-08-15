#!/usr/bin/env python3
"""Bulk-approve pending skirt (159) ML suggestions on school-uniform (16) images.

One-off for the 2026-07 seifuku→school-uniform reparent backfill
(docs/plans/2026-Q3/2026-07-26-school-uniform-flip-impl.md). Selection here, all
review side effects in the tested bulk_review_suggestions service.

Usage:
    uv run python scripts/ml_bulk_approve_skirt.py --user-id 123 --dry-run
    uv run python scripts/ml_bulk_approve_skirt.py --user-id 123
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.database import get_async_session
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag_link import TagLinks
from app.services.ml_suggestion_review import bulk_review_suggestions

SKIRT_TAG_ID = 159
SCOPE_TAG_ID = 16  # school uniform


async def run(args: argparse.Namespace) -> None:
    async with get_async_session() as db:
        query = (
            select(MlTagSuggestions.suggestion_id)  # type: ignore[call-overload]
            .join(TagLinks, TagLinks.image_id == MlTagSuggestions.image_id)
            .where(
                MlTagSuggestions.tag_id == SKIRT_TAG_ID,
                MlTagSuggestions.status == "pending",
                MlTagSuggestions.confidence >= args.min_confidence,
                TagLinks.tag_id == SCOPE_TAG_ID,
            )
            .order_by(MlTagSuggestions.suggestion_id)
        )
        suggestion_ids = list((await db.execute(query)).scalars())
        print(f"matched pending suggestions: {len(suggestion_ids)}")

        if args.dry_run:
            print("dry run — nothing approved")
            return

        approved = 0
        for start in range(0, len(suggestion_ids), args.batch_size):
            batch = suggestion_ids[start : start + args.batch_size]
            reviews = [{"suggestion_id": sid, "action": "approve"} for sid in batch]
            response = await bulk_review_suggestions(db, reviews, args.user_id)
            approved += response.approved
            if response.errors:
                for err in response.errors[:10]:
                    print(f"  error: {err}")
            print(f"  approved {approved}/{len(suggestion_ids)}")

        print(f"done: approved={approved}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-id", type=int, required=True, help="Reviewer user_id recorded on the approvals"
    )
    parser.add_argument("--min-confidence", type=float, default=0.9)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

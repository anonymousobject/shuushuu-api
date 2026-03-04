"""Bulk reindex all tags from MySQL to Meilisearch.

Usage:
    uv run python scripts/reindex_search.py
    uv run python scripts/reindex_search.py --batch-size 500

Idempotent — safe to run anytime. Meilisearch upserts by primary key.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from meilisearch_python_sdk import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.tag import Tags
from app.services.search import SearchService, _get_parent_usage_counts, configure_tags_index


async def reindex_tags(batch_size: int = 1000) -> None:
    """Reindex all tags from MySQL to Meilisearch."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    client = AsyncClient(
        url=settings.MEILISEARCH_URL,
        api_key=settings.MEILISEARCH_API_KEY,
    )

    try:
        # Configure index settings
        await configure_tags_index(client)
        service = SearchService(client)

        async with AsyncSession(engine) as db:
            # Get total count
            count_result = await db.execute(select(func.count(Tags.tag_id)))  # type: ignore[arg-type]
            total = count_result.scalar() or 0
            print(f"Reindexing {total} tags...")

            # Batch fetch and index
            indexed = 0
            offset = 0
            start = time.monotonic()

            while offset < total:
                result = await db.execute(
                    select(Tags)
                    .order_by(Tags.tag_id)  # type: ignore[arg-type]
                    .offset(offset)
                    .limit(batch_size)
                )
                tags = list(result.scalars().all())

                if not tags:
                    break

                parent_counts = await _get_parent_usage_counts(db, tags)
                await service.index_tags(tags, parent_usage_counts=parent_counts)
                indexed += len(tags)
                offset += batch_size
                print(f"  Indexed {indexed}/{total} tags...")

            elapsed = time.monotonic() - start
            print(f"Done. Indexed {indexed} tags in {elapsed:.1f}s")

    finally:
        await client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindex tags to Meilisearch")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for indexing")
    args = parser.parse_args()

    asyncio.run(reindex_tags(batch_size=args.batch_size))


if __name__ == "__main__":
    main()

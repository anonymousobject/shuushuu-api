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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.tag import Tags
from app.services.search import SearchService, configure_tags_index


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
            # Keyset pagination by tag_id: each batch fetches the next slice
            # via WHERE tag_id > last_id, avoiding the linear OFFSET scan that
            # would re-read up to ~230k rows on the final batch.
            indexed = 0
            last_id = 0
            start = time.monotonic()

            while True:
                result = await db.execute(
                    select(Tags)
                    .where(Tags.tag_id > last_id)  # type: ignore[arg-type]
                    .order_by(Tags.tag_id)  # type: ignore[arg-type]
                    .limit(batch_size)
                )
                tags = list(result.scalars().all())

                if not tags:
                    break

                await service.index_tags_from_db(db, tags)
                indexed += len(tags)
                last_id = tags[-1].tag_id  # type: ignore[assignment]
                print(f"  Indexed {indexed} tags...")

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

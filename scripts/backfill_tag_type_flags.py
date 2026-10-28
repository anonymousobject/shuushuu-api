"""Backfill images.has_theme/source/artist/character from tag_links.

Idempotent and resumable. Run AFTER the migration that adds the columns.
Until this completes, the columns default to 0, so the missing_tag_types filter
harmlessly over-reports images as "missing" a type — don't rely on it for real
results before the backfill finishes.
Usage: uv run python scripts/backfill_tag_type_flags.py [--batch 10000] [--start 0]
"""

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# UPDATE ... FROM + bool_or, same shape as app/services/tag_type_flags.py.
_BATCH_SQL = text(
    """
    UPDATE images
    SET has_theme = COALESCE(agg.ht, FALSE), has_source = COALESCE(agg.hs, FALSE),
        has_artist = COALESCE(agg.ha, FALSE), has_character = COALESCE(agg.hc, FALSE)
    FROM (
        SELECT i2.image_id,
               bool_or(t.type = 1) AS ht, bool_or(t.type = 2) AS hs,
               bool_or(t.type = 3) AS ha, bool_or(t.type = 4) AS hc
        FROM images i2
        LEFT JOIN tag_links tl ON tl.image_id = i2.image_id
        LEFT JOIN tags t ON t.tag_id = tl.tag_id
        WHERE i2.image_id >= :lo AND i2.image_id < :hi
        GROUP BY i2.image_id
    ) AS agg
    WHERE images.image_id = agg.image_id
    """
)


async def backfill_range(db: AsyncSession, lo: int, hi: int) -> None:
    """Recompute flags for all images with image_id in [lo, hi). Does not commit."""
    await db.execute(_BATCH_SQL, {"lo": lo, "hi": hi})


async def backfill(batch: int, start: int) -> None:
    if batch <= 0:
        raise ValueError("batch must be positive")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with async_session() as db:
            max_id = (await db.execute(text("SELECT MAX(image_id) FROM images"))).scalar() or 0
            lo = start
            while lo <= max_id:
                hi = lo + batch
                await backfill_range(db, lo, hi)
                await db.commit()
                print(f"backfilled image_id [{lo}, {hi})  (max={max_id})", flush=True)
                lo = hi
    finally:
        await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()
    if args.batch <= 0:
        ap.error("--batch must be positive")
    asyncio.run(backfill(args.batch, args.start))


if __name__ == "__main__":
    main()

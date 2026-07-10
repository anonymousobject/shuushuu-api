#!/usr/bin/env python3
"""
Manually trigger a full rebuild of the user_tag_affinity table.

Usage:
    uv run python scripts/refresh_user_tag_affinity.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.user_tag_affinity import refresh_user_tag_affinity


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        n = await refresh_user_tag_affinity(
            db,
            min_support=settings.TASTE_MIN_SUPPORT,
            smoothing_k=settings.TASTE_SMOOTHING_K,
            beta=settings.TASTE_RATING_BETA,
            min_events=settings.TASTE_MIN_EVENTS,
            batch_size=settings.TASTE_BATCH_SIZE,
        )

    await engine.dispose()

    if n < 0:
        print("Skipped: another refresh is already running.")
    else:
        print(f"Refreshed user_tag_affinity: {n} rows")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Manually trigger a full rebuild of the tag_cooccurrence table.

Usage:
    uv run python scripts/refresh_tag_cooccurrence.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.tag_cooccurrence import refresh_tag_cooccurrence


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        n = await refresh_tag_cooccurrence(
            db,
            min_cooccur=settings.COOCCUR_MIN_COOCCUR,
            top_n=settings.COOCCUR_TOP_N,
            min_base_usage=settings.COOCCUR_MIN_BASE_USAGE,
        )

    await engine.dispose()

    if n < 0:
        print("Skipped: another refresh is already running.")
    else:
        print(f"Refreshed tag_cooccurrence: {n} rows")


if __name__ == "__main__":
    asyncio.run(main())

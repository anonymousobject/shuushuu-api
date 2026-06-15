"""Arq task for weekly tag co-occurrence refresh."""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def refresh_tag_cooccurrence_job(ctx: dict[str, Any]) -> None:
    """
    Weekly refresh of the tag_cooccurrence table.

    Runs at 04:00 UTC every Sunday via arq cron. Skips silently if another
    refresh is already running (advisory lock not acquired).
    """
    from app.config import settings
    from app.core.database import get_async_session
    from app.services.tag_cooccurrence import refresh_tag_cooccurrence

    async with get_async_session() as db:
        n = await refresh_tag_cooccurrence(
            db,
            min_cooccur=settings.COOCCUR_MIN_COOCCUR,
            top_n=settings.COOCCUR_TOP_N,
            min_base_usage=settings.COOCCUR_MIN_BASE_USAGE,
        )

    if n < 0:
        logger.info("tag_cooccurrence_refresh_skipped_lock_held")
    else:
        logger.info("tag_cooccurrence_refreshed", rows=n)

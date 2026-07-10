"""Arq task for the nightly user-taste-profile refresh."""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


async def refresh_user_tag_affinity_job(ctx: dict[str, Any]) -> None:
    """
    Nightly refresh of the user_tag_affinity table (05:00 UTC).

    Skips silently if another refresh is already running (advisory lock not
    acquired). ~30+ minutes on dev-scale data (5.7M favorites), batched to
    keep MariaDB memory bounded.
    """
    from app.config import settings
    from app.core.database import get_async_session
    from app.services.user_tag_affinity import refresh_user_tag_affinity

    if not settings.TASTE_REFRESH_ENABLED:
        logger.info("user_tag_affinity_refresh_disabled")
        return

    async with get_async_session() as db:
        try:
            n = await refresh_user_tag_affinity(
                db,
                min_support=settings.TASTE_MIN_SUPPORT,
                smoothing_k=settings.TASTE_SMOOTHING_K,
                beta=settings.TASTE_RATING_BETA,
                min_events=settings.TASTE_MIN_EVENTS,
                batch_size=settings.TASTE_BATCH_SIZE,
            )
        except Exception as e:
            logger.exception(
                "user_tag_affinity_refresh_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return

    if n >= 0:
        logger.info("user_tag_affinity_refreshed", rows=n)

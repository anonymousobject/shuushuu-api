"""Rating calculation background jobs for arq worker."""

from typing import Any

from arq import Retry

from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def recalculate_rating_job(
    ctx: dict[str, Any],
    image_id: int,
) -> dict[str, bool]:
    """
    Recalculate Bayesian rating for an image.

    Args:
        ctx: ARQ context dict
        image_id: Image ID to recalculate

    Returns:
        dict with success status

    Raises:
        Retry: If database operation fails
    """
    bind_context(task="rating_recalculation", image_id=image_id)

    try:
        from app.services.rating import recalculate_image_ratings

        async with get_async_session() as db:
            await recalculate_image_ratings(db, image_id)
            await db.commit()

        logger.info("rating_recalculation_completed", image_id=image_id)
        return {"success": True}

    except Exception as e:
        logger.error(
            "rating_recalculation_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with backoff
        raise Retry(defer=ctx["job_try"] * 5) from e

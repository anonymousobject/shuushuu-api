"""
ML tag suggestion generation background job.

Thin arq wrapper around the shared generation pipeline
(app/services/ml_suggestion_pipeline.py). Triggered after image upload when
ML_TAG_SUGGESTIONS_ENABLED is on. Handles errors gracefully (logs and returns
an error dict) so a single bad image never crashes the worker queue.
"""

import json
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.models.image import Images
from app.services.ml_suggestion_pipeline import (
    generate_and_store_suggestions,
    persist_predictions,
)

logger = get_logger(__name__)


def _analyze_redis() -> redis.Redis:  # type: ignore[type-arg]
    """Return a Redis client pointed at the analyze-cache DB (db 0, same as the API endpoint)."""
    return redis.from_url(str(settings.REDIS_URL), encoding="utf-8", decode_responses=True)


async def generate_ml_tag_suggestions(
    ctx: dict[str, Any],
    image_id: int,
) -> dict[str, str | int]:
    """
    Generate ML tag suggestions for an image.

    Args:
        ctx: ARQ context dict (contains the loaded ml_service when the feature
            is enabled — set in worker startup)
        image_id: ID of the image to generate suggestions for

    Returns:
        dict with status and suggestions_created count, or status/error info.

    Note:
        Errors are logged and returned, never raised, so the worker queue
        keeps running.
    """
    bind_context(task="ml_tag_suggestion_generation", image_id=image_id)

    try:
        async with get_async_session() as db:
            result = await db.execute(
                select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
            )
            image = result.scalar_one_or_none()

            if not image:
                logger.error("ml_tag_suggestion_job_image_not_found", image_id=image_id)
                return {"status": "error", "error": f"Image {image_id} not found"}

            ml_service = ctx.get("ml_service")
            if ml_service is None:
                logger.error("ml_tag_suggestion_job_no_ml_service", image_id=image_id)
                return {
                    "status": "error",
                    "error": (
                        "ML tag suggestions not initialized "
                        "(ML_TAG_SUGGESTIONS_ENABLED is off or model failed to load)"
                    ),
                }

            cached_raw = None
            if image.md5_hash:
                try:
                    client = _analyze_redis()
                    try:
                        blob = await client.get(f"ml:analyze:{image.md5_hash}")
                    finally:
                        await client.aclose()  # type: ignore[attr-defined]  # stub lags runtime; aclose is correct
                    if blob:
                        cached_raw = json.loads(blob)
                except Exception:
                    logger.warning(
                        "ml_tag_suggestion_job_cache_read_failed",
                        image_id=image_id,
                        exc_info=True,
                    )

            if cached_raw is not None:
                suggestions_created = await persist_predictions(db, image_id, cached_raw)
            else:
                suggestions_created = await generate_and_store_suggestions(db, image, ml_service)

            logger.info(
                "ml_tag_suggestion_job_completed",
                image_id=image_id,
                suggestions_created=suggestions_created,
            )
            return {"status": "completed", "suggestions_created": suggestions_created}

    except Exception as e:
        logger.error(
            "ml_tag_suggestion_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Don't raise — the job handles errors gracefully so it won't crash the queue.
        return {"status": "error", "error": str(e)}

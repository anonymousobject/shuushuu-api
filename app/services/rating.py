"""
Rating calculation service.

Provides utilities for calculating and updating image rating statistics.
"""

import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImageRatings, Images

logger = logging.getLogger(__name__)


async def recalculate_image_ratings(db: AsyncSession, image_id: int) -> None:
    """
    Recalculate and update rating statistics for an image.

    Calculates:
    - num_ratings: Total number of ratings
    - rating: Simple average of all ratings
    - bayesian_rating: Weighted average that prevents low-sample bias

    Bayesian average formula: (C*m + sum) / (C + n)
    - C = global average ratings per image (confidence factor)
    - m = global average rating across all ratings
    - sum = sum of all ratings for this image
    - n = number of ratings for this image

    This prevents new images with one 10-star rating from ranking above
    established images with hundreds of 9-star ratings.

    Args:
        db: Database session
        image_id: ID of the image to recalculate ratings for
    """
    # Get global statistics for Bayesian calculation
    # C: Average number of ratings per image (confidence factor)
    avg_ratings_per_image_result = await db.execute(
        select(
            func.count(ImageRatings.user_id) / func.count(func.distinct(ImageRatings.image_id))  # type: ignore[arg-type]
        )
    )
    avg_ratings_per_image = float(avg_ratings_per_image_result.scalar() or 10.0)

    # m: Global average rating across all ratings
    global_avg_rating_result = await db.execute(select(func.avg(ImageRatings.rating)))
    global_avg_rating = float(global_avg_rating_result.scalar() or 5.0)

    # Get rating statistics for this specific image
    stats_result = await db.execute(
        select(
            func.count(ImageRatings.rating),  # type: ignore[arg-type]
            func.avg(ImageRatings.rating),
            func.sum(ImageRatings.rating),
        ).where(ImageRatings.image_id == image_id)  # type: ignore[arg-type]
    )
    row = stats_result.first()

    if row is None:
        count, avg_rating, sum_rating = 0, None, None
    else:
        count, avg_rating, sum_rating = row

    # Calculate bayesian average using global statistics
    # Formula: (C * m + n * rating) / (C + n)
    # where C = avg_ratings_per_image, m = global_avg_rating, n = count
    if count and count > 0:
        bayesian = (
            (avg_ratings_per_image * global_avg_rating) + (float(count) * float(avg_rating or 0))
        ) / (avg_ratings_per_image + float(count))
    else:
        bayesian = 0.0

    # Update image statistics
    await db.execute(
        update(Images)
        .where(Images.image_id == image_id)  # type: ignore[arg-type]
        .values(
            num_ratings=count or 0,
            rating=float(avg_rating or 0),
            bayesian_rating=bayesian,
        )
    )


def schedule_rating_recalculation(image_id: int) -> None:
    """
    Schedule a background task to recalculate image ratings.

    This runs asynchronously without blocking the API response.
    Uses asyncio.create_task() which requires the event loop to be running.

    Note: This is a simple fire-and-forget approach. For production,
    maybe something like arq or celery would be better.

    Args:
        image_id: ID of the image to recalculate ratings for
    """
    from app.core.database import get_async_session

    async def _background_task() -> None:
        """Background task to recalculate ratings with its own DB session."""
        logger.info(f"🔄 Starting background rating calculation for image {image_id}")
        async with get_async_session() as db:
            try:
                await recalculate_image_ratings(db, image_id)
                await db.commit()
                logger.info(f"✅ Completed rating calculation for image {image_id}")
            except Exception as e:
                # Log error but don't crash
                logger.error(
                    f"❌ Error recalculating ratings for image {image_id}: {e}",
                    exc_info=True,
                )
                await db.rollback()

    # Schedule the task in the background
    asyncio.create_task(_background_task())

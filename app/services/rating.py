"""
Rating calculation service.

Provides utilities for calculating and updating image rating statistics.
"""

import json
import logging
from dataclasses import dataclass

import redis.asyncio as redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImageRatings, Images

logger = logging.getLogger(__name__)

# Cache key and TTL for global rating stats (C and m values for Bayesian calc).
# These change negligibly per individual rating so a 5-minute TTL is fine.
_GLOBAL_RATING_STATS_KEY = "rating:global_stats"
_GLOBAL_RATING_STATS_TTL = 300


@dataclass
class RatingStats:
    """Computed rating statistics for an image."""

    average_rating: float
    bayesian_rating: float
    num_ratings: int


async def _get_global_rating_stats(
    db: AsyncSession,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> tuple[float, float]:
    """
    Get global rating statistics (C and m) for Bayesian calculation.

    Uses Redis cache when available to avoid full table scans on image_ratings.

    Returns:
        Tuple of (avg_ratings_per_image, global_avg_rating)
    """
    # Try cache first
    if redis_client is not None:
        cached = await redis_client.get(_GLOBAL_RATING_STATS_KEY)
        if cached:
            try:
                data = json.loads(cached)
                return float(data["c"]), float(data["m"])
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # Cache miss or no Redis — query database
    avg_ratings_per_image_result = await db.execute(
        select(
            func.count(ImageRatings.user_id) / func.count(func.distinct(ImageRatings.image_id))  # type: ignore[arg-type]
        )
    )
    avg_ratings_per_image = float(avg_ratings_per_image_result.scalar() or 10.0)

    global_avg_rating_result = await db.execute(select(func.avg(ImageRatings.rating)))
    global_avg_rating = float(global_avg_rating_result.scalar() or 5.0)

    # Store in cache
    if redis_client is not None:
        await redis_client.setex(
            _GLOBAL_RATING_STATS_KEY,
            _GLOBAL_RATING_STATS_TTL,
            json.dumps({"c": avg_ratings_per_image, "m": global_avg_rating}),
        )

    return avg_ratings_per_image, global_avg_rating


async def recalculate_image_ratings(
    db: AsyncSession,
    image_id: int,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> RatingStats:
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
        redis_client: Optional Redis client for caching global stats
    """
    avg_ratings_per_image, global_avg_rating = await _get_global_rating_stats(db, redis_client)

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

    stats = RatingStats(
        average_rating=float(avg_rating or 0),
        bayesian_rating=bayesian,
        num_ratings=count or 0,
    )

    # Update image statistics
    await db.execute(
        update(Images)
        .where(Images.image_id == image_id)  # type: ignore[arg-type]
        .values(
            num_ratings=stats.num_ratings,
            rating=stats.average_rating,
            bayesian_rating=stats.bayesian_rating,
        )
    )

    return stats


async def schedule_rating_recalculation(image_id: int) -> None:
    """
    Schedule a background job to recalculate image ratings using arq.

    This enqueues the job to the arq worker for async processing with retries.

    Args:
        image_id: ID of the image to recalculate ratings for
    """
    from app.tasks.queue import enqueue_job

    await enqueue_job("recalculate_rating_job", image_id=image_id)

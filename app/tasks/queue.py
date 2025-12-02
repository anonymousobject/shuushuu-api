"""
Queue client for enqueuing arq jobs from API endpoints.

Provides a simple interface for adding jobs to the arq queue.
"""

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Global pool instance (created on first use)
_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    """
    Get or create arq Redis connection pool.

    Returns:
        ArqRedis pool instance
    """
    global _pool
    if _pool is None:
        redis_settings = RedisSettings.from_dsn(settings.ARQ_REDIS_URL)
        _pool = await create_pool(redis_settings)
        logger.info("arq_pool_created", redis_url=settings.ARQ_REDIS_URL)
    return _pool


async def enqueue_job(
    function_name: str,
    *args: Any,
    _job_id: str | None = None,
    _defer_by: float | None = None,
    **kwargs: Any,
) -> str | None:
    """
    Enqueue a job to arq worker.

    Args:
        function_name: Name of registered arq function
        *args: Positional arguments for the function
        _job_id: Optional custom job ID
        _defer_by: Optional delay in seconds before job runs
        **kwargs: Keyword arguments for the function

    Returns:
        Job ID if enqueued successfully, None otherwise

    Example:
        await enqueue_job("create_thumbnail", image_id=123, source_path="/path/to/image.jpg")
    """
    try:
        pool = await get_queue()
        job = await pool.enqueue_job(
            function_name,
            *args,
            _job_id=_job_id,
            _defer_by=_defer_by,
            **kwargs,
        )

        if job:
            logger.debug("job_enqueued", function=function_name, job_id=job.job_id, kwargs=kwargs)
            return job.job_id
        else:
            logger.warning("job_enqueue_failed", function=function_name, kwargs=kwargs)
            return None

    except Exception as e:
        logger.error(
            "job_enqueue_error",
            function=function_name,
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


async def close_queue() -> None:
    """Close arq Redis connection pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("arq_pool_closed")

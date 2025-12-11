"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

from typing import Any

from arq.connections import RedisSettings
from arq.worker import func

from app.config import settings
from app.tasks.image_jobs import (
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)
from app.tasks.rating_jobs import recalculate_rating_job


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup - initialize any shared resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_starting", redis_url=settings.ARQ_REDIS_URL)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown - cleanup resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_shutdown")


class WorkerSettings:
    """ARQ worker configuration."""

    # Redis connection from settings
    redis_settings = RedisSettings.from_dsn(settings.ARQ_REDIS_URL)

    # Worker behavior
    max_jobs = 10  # Process up to 10 jobs concurrently
    job_timeout = 300  # 5 minutes max per job
    keep_result = settings.ARQ_KEEP_RESULT  # Keep results for 1 hour

    # Lifecycle hooks
    on_startup = startup
    on_shutdown = shutdown

    # Job functions
    functions = [
        func(create_thumbnail_job, max_tries=3),
        func(create_variant_job, max_tries=3),
        func(add_to_iqdb_job, max_tries=3),
        func(recalculate_rating_job, max_tries=3),
    ]

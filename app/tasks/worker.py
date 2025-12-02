"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Function

from app.config import settings
from app.tasks.image_jobs import (
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)


async def startup(ctx: dict) -> None:
    """Worker startup - initialize any shared resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_starting", redis_url=settings.ARQ_REDIS_URL)


async def shutdown(ctx: dict) -> None:
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
    functions: list[Function] = [
        Function(create_thumbnail_job, name="create_thumbnail", max_tries=3),
        Function(create_variant_job, name="create_variant", max_tries=3),
        Function(add_to_iqdb_job, name="add_to_iqdb", max_tries=3),
    ]

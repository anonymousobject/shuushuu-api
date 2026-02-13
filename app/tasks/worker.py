"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

import asyncio
from typing import Any

# arq's Worker.__init__ calls asyncio.get_event_loop() which raises RuntimeError
# in Python 3.14 when no event loop exists. Ensure one is available before arq runs.
# https://github.com/python-arq/arq/issues/144
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from app.config import settings
from app.core.database import get_async_session
from app.services.user_cleanup import cleanup_unverified_accounts
from app.tasks.email_jobs import send_verification_email_job
from app.tasks.image_jobs import (
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)
from app.tasks.pm_jobs import send_pm_notification
from app.tasks.rating_jobs import recalculate_rating_job


async def cleanup_stale_accounts(ctx: dict[str, Any]) -> None:
    """
    Daily cleanup of unverified inactive accounts.

    Runs at 3 AM UTC daily via arq cron.
    """
    from app.core.logging import get_logger

    logger = get_logger(__name__)

    async with get_async_session() as db:
        try:
            count = await cleanup_unverified_accounts(db)
            logger.info("cleanup_task_complete", deleted_accounts=count)
        except Exception as e:
            logger.error("cleanup_task_failed", error=str(e))
            # Don't re-raise, just log failure for cron job


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
        func(send_pm_notification, max_tries=3),
        func(send_verification_email_job, max_tries=3),
    ]

    cron_jobs = [
        cron(cleanup_stale_accounts, hour=3, minute=0),
    ]

"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from app.config import settings
from app.core.database import get_async_session
from app.services.ml_service import MLTagSuggestionService
from app.services.user_cleanup import cleanup_unverified_accounts
from app.tasks.email_jobs import send_verification_email_job
from app.tasks.image_jobs import (
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)
from app.tasks.pm_jobs import send_pm_notification
from app.tasks.rating_jobs import recalculate_rating_job
from app.tasks.tag_suggestion_job import generate_tag_suggestions


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

    # Initialize ML service for tag suggestions
    ml_service = MLTagSuggestionService()
    await ml_service.load_models()
    ctx["ml_service"] = ml_service
    logger.info("ml_service_initialized")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown - cleanup resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_shutdown")

    # Cleanup ML service if present
    if "ml_service" in ctx:
        await ctx["ml_service"].cleanup()
        logger.info("ml_service_cleaned_up")


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
        func(generate_tag_suggestions, max_tries=3),
        func(send_pm_notification, max_tries=3),
        func(send_verification_email_job, max_tries=3),
    ]

    cron_jobs = [
        cron(cleanup_stale_accounts, hour=3, minute=0),
    ]

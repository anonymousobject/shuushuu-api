"""
ARQ worker configuration and job definitions.

Run worker with: uv run arq app.tasks.worker.WorkerSettings
"""

import asyncio
import socket
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
from app.core.logging import configure_logging, get_logger
from app.tasks.lockfile_check import check_lockfile_freshness

# Same pattern as app/main.py: configure structlog at module import. arq is
# launched as `uv run arq app.tasks.worker.WorkerSettings`, which never goes
# through main.py, so without this call the worker would fall through to
# structlog's defaults (ConsoleRenderer) instead of the prod JSONRenderer.
configure_logging()

# Verify the Docker image isn't stale before importing anything that loads
# third-party deps at import time: app.core.database builds the engine (and
# so imports the asyncpg driver) on import, so a dependency added to uv.lock
# without a rebuild would otherwise crash here with a bare ModuleNotFoundError
# and the check below would never run.
check_lockfile_freshness(get_logger(__name__))

from app.core.database import get_async_session  # noqa: E402
from app.services.image_status import enqueue_r2_sync_on_status_change  # noqa: E402
from app.services.review_jobs import check_review_deadlines  # noqa: E402
from app.services.user_cleanup import cleanup_unverified_accounts  # noqa: E402
from app.tasks.email_jobs import (  # noqa: E402
    send_password_reset_email_job,
    send_verification_email_job,
)
from app.tasks.image_jobs import (  # noqa: E402
    add_to_iqdb_job,
    create_thumbnail_job,
    create_variant_job,
)
from app.tasks.ml_tag_suggestion_job import generate_ml_tag_suggestions  # noqa: E402
from app.tasks.pm_jobs import send_pm_notification  # noqa: E402
from app.tasks.r2_jobs import (  # noqa: E402
    r2_delete_image_job,
    r2_finalize_upload_job,
    sync_image_status_job,
)
from app.tasks.rating_jobs import recalculate_rating_job  # noqa: E402
from app.tasks.taste_profile import refresh_user_tag_affinity_job  # noqa: E402


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


async def process_review_deadlines(ctx: dict[str, Any]) -> None:
    """
    Process reviews past their voting deadline.

    Runs hourly via arq cron. Closes reviews with quorum or extends/defaults as needed.
    """
    from app.core.logging import get_logger

    logger = get_logger(__name__)

    async with get_async_session() as db:
        try:
            results = await check_review_deadlines(db)
            await db.commit()
            # Enqueue R2 bucket moves AFTER the commit — the worker that runs
            # sync_image_status_job loads the row in a fresh session and needs
            # to see the post-transition status.
            for image_id, old_status, new_status in results.get("transitions", []):
                await enqueue_r2_sync_on_status_change(image_id, old_status, new_status)
            logger.info(
                "review_deadline_job_complete",
                processed=results["processed"],
                closed=results["closed"],
                extended=results["extended"],
                errors=results["errors"],
            )
        except Exception as e:
            await db.rollback()
            logger.exception(
                "review_deadline_job_failed",
                error=str(e),
                error_type=type(e).__name__,
            )


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup - initialize any shared resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("arq_worker_starting", redis_url=settings.ARQ_REDIS_URL)

    # Load the ML tagging model once per worker when the feature is enabled.
    # Deliberately NOT wrapped in try/except: if the flag is on but model
    # files are absent, the worker must fail to start rather than silently
    # skip suggestion generation (no mock fallback by design).
    if settings.ML_TAG_SUGGESTIONS_ENABLED:
        from app.services.ml_service import MLTagSuggestionService

        ml_service = MLTagSuggestionService()
        await ml_service.load_models()
        ctx["ml_service"] = ml_service
        logger.info("ml_service_initialized", model_name=ml_service.model_name)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown - cleanup resources."""
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    if "ml_service" in ctx:
        await ctx["ml_service"].cleanup()
    logger.info("arq_worker_shutdown")


class WorkerSettings:
    """ARQ worker configuration."""

    # Redis connection from settings
    redis_settings = RedisSettings.from_dsn(settings.ARQ_REDIS_URL)

    # Worker behavior
    max_jobs = 10  # Process up to 10 jobs concurrently
    job_timeout = 300  # 5 minutes max per job
    keep_result = settings.ARQ_KEEP_RESULT  # Keep results for 1 hour

    # Health: the compose healthcheck runs `arq --check` against this class.
    # arq's default health key is queue-scoped, so during a zero-downtime
    # rollout the OLD replica would keep it fresh and mask a broken NEW one —
    # rollout would drain the healthy worker and leave zero. Keying by hostname
    # (= container id) makes the check reflect this container's worker only;
    # `arq --check` runs in the same container, so it computes the same key.
    # Stale keys self-clean: arq sets the key with TTL = interval + 1s.
    health_check_interval = 15
    health_check_key = f"arq:health:{socket.gethostname()}"

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
        func(send_password_reset_email_job, max_tries=3),
        func(r2_finalize_upload_job, max_tries=5),
        func(sync_image_status_job, max_tries=settings.ARQ_MAX_TRIES),
        func(r2_delete_image_job, max_tries=settings.ARQ_MAX_TRIES),
        func(generate_ml_tag_suggestions, max_tries=3),
        # job_timeout (300s) would kill this ~30+ minute refresh; override per-function.
        func(refresh_user_tag_affinity_job, max_tries=1, timeout=7200),
    ]

    cron_jobs = [
        cron(cleanup_stale_accounts, hour=3, minute=0),
        cron(process_review_deadlines, minute=0),  # Every hour on the hour
        # Cron jobs are dispatched under a separate "cron:<name>" registry key from
        # func() entries, so the timeout above does NOT apply here — set it again.
        cron(refresh_user_tag_affinity_job, hour=5, minute=0, timeout=7200),  # nightly, 05:00 UTC
    ]

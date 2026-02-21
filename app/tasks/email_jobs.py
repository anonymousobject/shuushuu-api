"""Email notification background jobs for arq worker."""

from typing import Any

from arq import Retry
from sqlalchemy import select

from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.models.user import Users
from app.services.email import send_password_reset_email, send_verification_email

logger = get_logger(__name__)


async def send_verification_email_job(ctx: dict[str, Any], user_id: int, token: str) -> None:
    """
    Background task to send email verification.

    Sends verification email to user if their email is not yet verified.

    Args:
        ctx: ARQ context dict
        user_id: ID of the user
        token: Raw verification token (not hashed)

    Raises:
        Retry: If database query or email fails (will retry up to max_tries)
    """
    bind_context(task="send_verification_email", user_id=user_id)

    try:
        async with get_async_session() as db:
            # Fetch user
            user_query = select(Users).where(Users.user_id == user_id)  # type: ignore[arg-type]
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()

            if not user:
                logger.warning("verification_email_user_not_found", user_id=user_id)
                return

            # Check if email is already verified (race condition guard)
            if user.email_verified:
                logger.info(
                    "verification_email_skipped",
                    user_id=user_id,
                    reason="already_verified",
                )
                return

            # Send email
            success = await send_verification_email(user=user, token=token)

            if success:
                logger.info(
                    "verification_email_sent",
                    user_id=user_id,
                    email=user.email,
                )
            else:
                logger.error(
                    "verification_email_failed",
                    user_id=user_id,
                    email=user.email,
                )
                # Retry with exponential backoff (arq default: 0s, 5s, 15s, 30s, etc.)
                raise Retry(defer=ctx["job_try"] * 5)

    except Exception as e:
        if isinstance(e, Retry):
            raise
        logger.error(
            "verification_email_task_error",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with exponential backoff
        raise Retry(defer=ctx["job_try"] * 5) from e


async def send_password_reset_email_job(ctx: dict[str, Any], user_id: int, token: str) -> None:
    """
    Background task to send password reset email.

    Args:
        ctx: ARQ context dict
        user_id: ID of the user
        token: Raw reset token (not hashed)

    Raises:
        Retry: If database query or email fails (will retry up to max_tries)
    """
    bind_context(task="send_password_reset_email", user_id=user_id)

    try:
        async with get_async_session() as db:
            user_query = select(Users).where(Users.user_id == user_id)  # type: ignore[arg-type]
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()

            if not user:
                logger.warning("password_reset_email_user_not_found", user_id=user_id)
                return

            success = await send_password_reset_email(user=user, token=token)

            if success:
                logger.info(
                    "password_reset_email_sent",
                    user_id=user_id,
                    email=user.email,
                )
            else:
                logger.error(
                    "password_reset_email_failed",
                    user_id=user_id,
                    email=user.email,
                )
                raise Retry(defer=ctx["job_try"] * 5)

    except Exception as e:
        if isinstance(e, Retry):
            raise
        logger.error(
            "password_reset_email_task_error",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise Retry(defer=ctx["job_try"] * 5) from e

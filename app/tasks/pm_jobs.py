"""Private message notification background jobs for arq worker."""

from typing import Any

from arq import Retry
from sqlalchemy import select

from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger
from app.models.privmsg import Privmsgs
from app.models.user import Users
from app.services.email import send_pm_notification_email

logger = get_logger(__name__)


async def send_pm_notification(ctx: dict[str, Any], privmsg_id: int) -> None:
    """
    Background task to send PM notification email.

    Sends email to recipient if:
    - They have email_pm_pref enabled (=1)
    - Their email is verified (email_verified=1)

    Args:
        ctx: ARQ context dict
        privmsg_id: ID of the private message

    Raises:
        Retry: If database query or email fails (will retry up to max_tries)
    """
    bind_context(task="pm_notification", privmsg_id=privmsg_id)

    try:
        async with get_async_session() as db:
            # Fetch PM
            privmsg_query = select(Privmsgs).where(Privmsgs.privmsg_id == privmsg_id)  # type: ignore[arg-type]
            privmsg_result = await db.execute(privmsg_query)
            privmsg = privmsg_result.scalar_one_or_none()

            if not privmsg:
                logger.warning("pm_not_found", privmsg_id=privmsg_id)
                return

            # Fetch sender to get username
            sender_query = select(Users).where(Users.user_id == privmsg.from_user_id)  # type: ignore[arg-type]
            sender_result = await db.execute(sender_query)
            sender = sender_result.scalar_one_or_none()

            if not sender:
                logger.warning(
                    "pm_sender_not_found",
                    privmsg_id=privmsg_id,
                    from_user_id=privmsg.from_user_id,
                )
                return

            sender_username = sender.username

            # Fetch recipient user
            recipient_query = select(Users).where(
                Users.user_id == privmsg.to_user_id  # type: ignore[arg-type]
            )
            recipient_result = await db.execute(recipient_query)
            recipient = recipient_result.scalar_one_or_none()

            if not recipient:
                logger.warning(
                    "pm_recipient_not_found",
                    privmsg_id=privmsg_id,
                    to_user_id=privmsg.to_user_id,
                )
                return

            # Check conditions for sending email
            if recipient.email_pm_pref != 1:
                logger.info(
                    "pm_notification_skipped",
                    privmsg_id=privmsg_id,
                    recipient_id=recipient.user_id,
                    reason="email_pm_pref_disabled",
                )
                return

            if recipient.email_verified != 1:
                logger.info(
                    "pm_notification_skipped",
                    privmsg_id=privmsg_id,
                    recipient_id=recipient.user_id,
                    reason="email_not_verified",
                )
                return

            # Send email
            success = await send_pm_notification_email(
                recipient=recipient,
                sender_username=sender_username,
                pm_subject=privmsg.subject,
            )

            if success:
                logger.info(
                    "pm_notification_sent",
                    privmsg_id=privmsg_id,
                    recipient_id=recipient.user_id,
                    recipient_email=recipient.email,
                )
            else:
                logger.error(
                    "pm_notification_failed",
                    privmsg_id=privmsg_id,
                    recipient_id=recipient.user_id,
                )
                # Retry with exponential backoff (arq default: 0s, 5s, 15s, 30s, etc.)
                raise Retry(defer=ctx["job_try"] * 5)

    except Exception as e:
        if isinstance(e, Retry):
            raise
        logger.error(
            "pm_notification_task_error",
            privmsg_id=privmsg_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with exponential backoff
        raise Retry(defer=ctx["job_try"] * 5) from e

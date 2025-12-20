"""User account cleanup service."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user import Users

logger = get_logger(__name__)


async def cleanup_unverified_accounts(db: AsyncSession) -> int:
    """
    Delete unverified accounts older than 30 days with no login activity.

    Deletion criteria (ALL must be true):
    - email_verified = False
    - Created 30+ days ago (date_joined < cutoff)
    - Never logged in OR last_login same as date_joined

    Verified users are NEVER deleted, regardless of inactivity.

    Args:
        db: Database session

    Returns:
        Count of deleted accounts
    """
    cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)

    # Build query for stale unverified accounts
    query = select(Users).where(
        Users.email_verified == False,  # type: ignore[arg-type]  # Not verified  # noqa: E712
        Users.date_joined < cutoff_date,  # type: ignore[arg-type]  # Created 30+ days ago
        or_(
            Users.last_login.is_(None),  # type: ignore[union-attr]  # Never logged in
            Users.last_login <= Users.date_joined,  # type: ignore[operator, arg-type]  # Logged in once at creation (legacy)
        ),
    )

    result = await db.execute(query)
    stale_users = result.scalars().all()

    count = 0
    for user in stale_users:
        logger.info(
            "deleting_stale_unverified_user",
            user_id=user.user_id,
            username=user.username,
            date_joined=user.date_joined,
            last_login=user.last_login,
        )
        await db.delete(user)
        count += 1

    if count > 0:
        await db.commit()
        logger.info("cleanup_unverified_accounts_complete", deleted_count=count)
    else:
        logger.debug("cleanup_unverified_accounts_no_deletions")

    return count

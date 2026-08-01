"""Couple open review sessions to the image-status lifecycle.

A review is a vote on what should happen to an image. Once a moderator settles
that question by hand, the vote is moot: leaving it open keeps collecting
votes on a decision already made, keeps the image in the review queue, and —
worst — leaves check_review_deadlines free to close it later and re-apply its
own outcome, so a KEEP close silently reactivates an image a moderator
deactivated.

This module owns the transition hook called by change_image_status. The
suggestion-row twin lives in app/services/ml_suggestion_lifecycle.py.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, ReviewOutcome, ReviewStatus
from app.models.admin_action import AdminActions
from app.models.image_review import ImageReviews


async def supersede_open_reviews_for_status_change(
    db: AsyncSession,
    image_id: int,
    actor_id: int | None,
    new_status: int,
) -> None:
    """Close any open review on ``image_id`` as SUPERSEDED.

    Called only when the status change is NOT itself a review action — see the
    review_id guard at the call site. Writes a REVIEW_CLOSE audit row per
    review closed, alongside the status-change row change_image_status writes.

    Only one review per image may be open (enforced at application level), but
    legacy rows predate that rule, so every open one is closed.

    Flush-only; the caller owns the transaction.
    """
    reviews = (
        (
            await db.execute(
                select(ImageReviews).where(
                    ImageReviews.image_id == image_id,  # type: ignore[arg-type]
                    ImageReviews.status == ReviewStatus.OPEN,  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for review in reviews:
        review.status = ReviewStatus.CLOSED
        review.outcome = ReviewOutcome.SUPERSEDED
        review.closed_at = now
        review.closed_by = actor_id
        db.add(
            AdminActions(
                user_id=actor_id,
                action_type=AdminActionType.REVIEW_CLOSE,
                review_id=review.review_id,
                image_id=image_id,
                details={
                    "outcome": ReviewOutcome.SUPERSEDED,
                    "outcome_label": "superseded",
                    "close_reason": "manual_status_change",
                    "new_status": new_status,
                    "automatic": False,
                },
            )
        )

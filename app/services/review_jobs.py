"""
Background jobs for the review system.

Provides scheduled tasks for processing review deadlines and pruning old audit logs.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    ImageStatus,
    ReviewOutcome,
    ReviewStatus,
    settings,
)
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_review import ImageReviews
from app.models.review_vote import ReviewVotes

logger = logging.getLogger(__name__)


async def check_review_deadlines(db: AsyncSession) -> dict[str, Any]:
    """
    Process all open reviews past their deadline.

    Handles quorum and majority logic:
    - If quorum met (3+ votes) with clear majority: close with that outcome
    - If no quorum or tie and no extension used: extend deadline
    - If no quorum or tie after extension: default to keep

    Each review is processed in its own savepoint to prevent one failure
    from blocking others.

    Args:
        db: Database session

    Returns:
        dict with counts: {
            "processed": int,
            "closed": int,
            "extended": int,
            "errors": int,
            "error_details": list
        }
    """
    results: dict[str, Any] = {
        "processed": 0,
        "closed": 0,
        "extended": 0,
        "errors": 0,
        "error_details": [],
    }

    # Query all open reviews past their deadline
    now = datetime.now(UTC)
    stmt = select(ImageReviews).where(
        ImageReviews.status == ReviewStatus.OPEN,  # type: ignore[arg-type]
        ImageReviews.deadline < now,  # type: ignore[arg-type, operator]
    )
    result = await db.execute(stmt)
    reviews = result.scalars().all()

    logger.info(f"Found {len(reviews)} expired reviews to process")

    for review in reviews:
        try:
            async with db.begin_nested():  # Savepoint for each review
                outcome = await _process_single_review(db, review)
                results["processed"] += 1
                if outcome == "closed":
                    results["closed"] += 1
                elif outcome == "extended":
                    results["extended"] += 1
        except Exception as e:
            results["errors"] += 1
            results["error_details"].append(
                {
                    "review_id": review.review_id,
                    "error": str(e),
                }
            )
            logger.error(
                f"Failed to process review {review.review_id}: {e}",
                exc_info=True,
            )

    logger.info(
        f"check_review_deadlines complete: "
        f"processed={results['processed']}, closed={results['closed']}, "
        f"extended={results['extended']}, errors={results['errors']}"
    )

    return results


async def _process_single_review(db: AsyncSession, review: ImageReviews) -> str:
    """
    Process a single expired review.

    Args:
        db: Database session
        review: The review to process

    Returns:
        "closed" if review was closed, "extended" if deadline was extended
    """
    # Count votes by type
    vote_counts = await _get_vote_counts(db, review.review_id)  # type: ignore[arg-type]
    keep_votes = vote_counts.get(1, 0)  # vote=1 is keep
    remove_votes = vote_counts.get(0, 0)  # vote=0 is remove
    total_votes = keep_votes + remove_votes

    quorum = settings.REVIEW_QUORUM
    has_quorum = total_votes >= quorum
    is_tie = keep_votes == remove_votes

    logger.debug(
        f"Review {review.review_id}: keep={keep_votes}, remove={remove_votes}, "
        f"quorum={has_quorum}, tie={is_tie}, extension_used={review.extension_used}"
    )

    if has_quorum and not is_tie:
        # Has quorum and clear majority - close with outcome
        outcome = ReviewOutcome.KEEP if keep_votes > remove_votes else ReviewOutcome.REMOVE
        await _close_review(db, review, outcome, "quorum_reached")
        return "closed"
    elif not review.extension_used:
        # No quorum or tie, first time - extend deadline
        await _extend_review(db, review)
        return "extended"
    else:
        # No quorum or tie after extension - default to keep
        await _close_review(db, review, ReviewOutcome.KEEP, "default_after_extension")
        return "closed"


async def _get_vote_counts(db: AsyncSession, review_id: int) -> dict[int, int]:
    """
    Count votes by type for a review.

    Args:
        db: Database session
        review_id: The review ID

    Returns:
        Dict mapping vote value (0 or 1) to count
    """
    stmt = (
        select(ReviewVotes.vote, func.count(ReviewVotes.vote))  # type: ignore[arg-type, call-overload]
        .where(ReviewVotes.review_id == review_id)
        .group_by(ReviewVotes.vote)
    )
    result = await db.execute(stmt)
    return dict(result.all())  # type: ignore[arg-type]


async def _close_review(
    db: AsyncSession,
    review: ImageReviews,
    outcome: int,
    reason: str,
) -> None:
    """
    Close a review and update the image status.

    Args:
        db: Database session
        review: The review to close
        outcome: ReviewOutcome value (KEEP or REMOVE)
        reason: Reason for closing (for audit log)
    """
    now = datetime.now(UTC)

    # Update review
    review.status = ReviewStatus.CLOSED
    review.outcome = outcome
    review.closed_at = now

    # Update image status
    image = await db.get(Images, review.image_id)
    if image:
        if outcome == ReviewOutcome.KEEP:
            image.status = ImageStatus.ACTIVE
        else:  # REMOVE
            image.status = ImageStatus.INAPPROPRIATE

    # Create audit log entry
    action = AdminActions(
        user_id=None,  # System action
        action_type=AdminActionType.REVIEW_CLOSE,
        review_id=review.review_id,
        image_id=review.image_id,
        details={
            "outcome": outcome,
            "outcome_label": "keep" if outcome == ReviewOutcome.KEEP else "remove",
            "reason": reason,
            "automatic": True,
        },
        created_at=now,
    )
    db.add(action)

    logger.info(
        f"Closed review {review.review_id} with outcome "
        f"{'KEEP' if outcome == ReviewOutcome.KEEP else 'REMOVE'} (reason: {reason})"
    )


async def _extend_review(db: AsyncSession, review: ImageReviews) -> None:
    """
    Extend a review's deadline.

    Args:
        db: Database session
        review: The review to extend
    """
    now = datetime.now(UTC)
    extension_days = settings.REVIEW_EXTENSION_DAYS
    new_deadline = now + timedelta(days=extension_days)

    review.deadline = new_deadline
    review.extension_used = 1

    # Create audit log entry
    action = AdminActions(
        user_id=None,  # System action
        action_type=AdminActionType.REVIEW_EXTEND,
        review_id=review.review_id,
        image_id=review.image_id,
        details={
            "reason": "deadline_expired_auto_extend",
            "extension_days": extension_days,
            "new_deadline": new_deadline.isoformat(),
            "automatic": True,
        },
        created_at=now,
    )
    db.add(action)

    logger.info(
        f"Extended review {review.review_id} deadline by {extension_days} days "
        f"to {new_deadline.isoformat()}"
    )


async def prune_admin_actions(
    db: AsyncSession,
    retention_years: int = 2,
) -> int:
    """
    Delete admin_actions older than retention period.

    Runs monthly to maintain audit log size.

    Args:
        db: Database session
        retention_years: How many years of history to keep (default 2)

    Returns:
        Number of rows deleted
    """
    # Use timezone-naive datetime for MySQL compatibility
    # MySQL DATETIME columns don't store timezone info
    cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_years * 365)

    stmt = (
        delete(AdminActions)
        .where(
            AdminActions.created_at < cutoff_date  # type: ignore[arg-type, operator]
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()

    deleted_count = result.rowcount or 0  # type: ignore[attr-defined]

    logger.info(
        f"Pruned {deleted_count} admin_actions older than "
        f"{cutoff_date.date()} ({retention_years} years)"
    )

    return deleted_count

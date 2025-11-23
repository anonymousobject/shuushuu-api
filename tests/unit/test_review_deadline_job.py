"""
Unit tests for the review deadline background job.

Tests cover all quorum, majority, tie, and edge case scenarios.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    ImageStatus,
    ReviewOutcome,
    ReviewStatus,
)
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_review import ImageReviews
from app.models.review_vote import ReviewVotes
from app.models.user import Users
from app.services.review_jobs import (
    _get_vote_counts,
    check_review_deadlines,
    prune_admin_actions,
)


async def create_test_image(
    db_session: AsyncSession,
    user_id: int = 1,
    status: int = ImageStatus.REVIEW,
) -> Images:
    """Create a test image."""
    image = Images(
        filename=f"test-review-job-{datetime.now(UTC).timestamp()}",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash=f"reviewjobhash{datetime.now(UTC).timestamp():.0f}",
        filesize=123456,
        width=1920,
        height=1080,
        user_id=user_id,
        status=status,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def create_test_review(
    db_session: AsyncSession,
    image_id: int,
    initiated_by: int = 1,
    deadline_offset_days: int = -1,
    extension_used: int = 0,
) -> ImageReviews:
    """Create a test review with specified deadline offset."""
    deadline = datetime.now(UTC) + timedelta(days=deadline_offset_days)
    review = ImageReviews(
        image_id=image_id,
        initiated_by=initiated_by,
        deadline=deadline,
        extension_used=extension_used,
        status=ReviewStatus.OPEN,
        outcome=ReviewOutcome.PENDING,
        review_type=1,
        created_at=datetime.now(UTC),
    )
    db_session.add(review)
    await db_session.commit()
    await db_session.refresh(review)
    return review


# Counter for unique user IDs across tests
_user_id_counter = 1000


async def create_test_users(
    db_session: AsyncSession,
    count: int,
) -> list[int]:
    """Create test users and return their IDs."""
    global _user_id_counter
    user_ids = []
    for _ in range(count):
        user_id = _user_id_counter
        _user_id_counter += 1
        user = Users(
            user_id=user_id,
            username=f"voter_{user_id}",
            password="testpassword",
            password_type="bcrypt",
            salt=f"salt{user_id:010d}"[:16],
            email=f"voter{user_id}@example.com",
        )
        db_session.add(user)
        user_ids.append(user_id)
    await db_session.commit()
    return user_ids


async def add_votes(
    db_session: AsyncSession,
    review_id: int,
    keep_count: int,
    remove_count: int,
) -> None:
    """Add votes to a review. Creates test users as needed."""
    total_voters = keep_count + remove_count
    if total_voters == 0:
        return

    # Create users for voting
    user_ids = await create_test_users(db_session, total_voters)

    idx = 0
    for _ in range(keep_count):
        vote = ReviewVotes(
            review_id=review_id,
            user_id=user_ids[idx],
            vote=1,  # keep
            created_at=datetime.now(UTC),
        )
        db_session.add(vote)
        idx += 1

    for _ in range(remove_count):
        vote = ReviewVotes(
            review_id=review_id,
            user_id=user_ids[idx],
            vote=0,  # remove
            created_at=datetime.now(UTC),
        )
        db_session.add(vote)
        idx += 1

    await db_session.commit()


@pytest.mark.asyncio
class TestQuorumAndMajority:
    """Tests for quorum and majority scenarios."""

    async def test_unanimous_keep(self, db_session: AsyncSession):
        """3 keep, 0 remove -> close with outcome=KEEP, image status=ACTIVE."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=3, remove_count=0)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["processed"] == 1
        assert result["closed"] == 1
        assert result["extended"] == 0
        assert review.status == ReviewStatus.CLOSED
        assert review.outcome == ReviewOutcome.KEEP
        assert image.status == ImageStatus.ACTIVE

    async def test_unanimous_remove(self, db_session: AsyncSession):
        """0 keep, 3 remove -> close with outcome=REMOVE, image status=INAPPROPRIATE."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=0, remove_count=3)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.status == ReviewStatus.CLOSED
        assert review.outcome == ReviewOutcome.REMOVE
        assert image.status == ImageStatus.INAPPROPRIATE

    async def test_majority_keep(self, db_session: AsyncSession):
        """2 keep, 1 remove -> close with outcome=KEEP (majority)."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=2, remove_count=1)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.outcome == ReviewOutcome.KEEP
        assert image.status == ImageStatus.ACTIVE

    async def test_majority_remove(self, db_session: AsyncSession):
        """1 keep, 2 remove -> close with outcome=REMOVE (majority)."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=1, remove_count=2)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.outcome == ReviewOutcome.REMOVE
        assert image.status == ImageStatus.INAPPROPRIATE


@pytest.mark.asyncio
class TestNoQuorum:
    """Tests for no quorum scenarios."""

    async def test_no_quorum_extend_first_time(self, db_session: AsyncSession):
        """2 votes total, deadline passed, extension_used=false -> extend deadline."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=0
        )
        await add_votes(db_session, review.review_id, keep_count=1, remove_count=1)  # type: ignore[arg-type]

        old_deadline = review.deadline
        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)

        assert result["extended"] == 1
        assert result["closed"] == 0
        assert review.status == ReviewStatus.OPEN
        assert review.extension_used == 1
        assert review.deadline > old_deadline  # type: ignore[operator]

    async def test_no_quorum_default_keep_after_extension(self, db_session: AsyncSession):
        """2 votes total, deadline passed, extension_used=true -> close with outcome=KEEP."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=1
        )
        await add_votes(db_session, review.review_id, keep_count=1, remove_count=1)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.status == ReviewStatus.CLOSED
        assert review.outcome == ReviewOutcome.KEEP
        assert image.status == ImageStatus.ACTIVE


@pytest.mark.asyncio
class TestTieScenarios:
    """Tests for tie scenarios."""

    async def test_tie_with_quorum_extend_first_time(self, db_session: AsyncSession):
        """2 keep, 2 remove, extension_used=false -> extend deadline."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=0
        )
        await add_votes(db_session, review.review_id, keep_count=2, remove_count=2)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)

        assert result["extended"] == 1
        assert review.status == ReviewStatus.OPEN
        assert review.extension_used == 1

    async def test_tie_with_quorum_default_keep_after_extension(self, db_session: AsyncSession):
        """2 keep, 2 remove, extension_used=true -> close with outcome=KEEP."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=1
        )
        await add_votes(db_session, review.review_id, keep_count=2, remove_count=2)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.outcome == ReviewOutcome.KEEP
        assert image.status == ImageStatus.ACTIVE


@pytest.mark.asyncio
class TestEdgeCases:
    """Tests for edge cases."""

    async def test_review_not_past_deadline_no_action(self, db_session: AsyncSession):
        """Review not past deadline -> no action taken."""
        image = await create_test_image(db_session)
        # Deadline in future
        review = await create_test_review(
            db_session, image.image_id, deadline_offset_days=7
        )
        await add_votes(db_session, review.review_id, keep_count=3, remove_count=0)  # type: ignore[arg-type]

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)

        assert result["processed"] == 0
        assert review.status == ReviewStatus.OPEN

    async def test_already_closed_review_skipped(self, db_session: AsyncSession):
        """Review already closed -> skipped."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        review.status = ReviewStatus.CLOSED
        review.outcome = ReviewOutcome.KEEP
        await db_session.commit()

        result = await check_review_deadlines(db_session)

        assert result["processed"] == 0

    async def test_zero_votes_extend_first_time(self, db_session: AsyncSession):
        """0 votes, deadline passed -> extend (first time)."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=0
        )
        # No votes added

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)

        assert result["extended"] == 1
        assert review.extension_used == 1

    async def test_zero_votes_default_keep_after_extension(self, db_session: AsyncSession):
        """0 votes, deadline passed, extension used -> default keep."""
        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=1
        )
        # No votes added

        result = await check_review_deadlines(db_session)

        await db_session.refresh(review)
        await db_session.refresh(image)

        assert result["closed"] == 1
        assert review.outcome == ReviewOutcome.KEEP
        assert image.status == ImageStatus.ACTIVE


@pytest.mark.asyncio
class TestAuditLogging:
    """Tests for audit log entries."""

    async def test_close_creates_audit_log(self, db_session: AsyncSession):
        """Closing a review creates an admin_action entry."""
        from sqlalchemy import select

        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=3, remove_count=0)  # type: ignore[arg-type]

        await check_review_deadlines(db_session)

        # Check audit log
        stmt = select(AdminActions).where(
            AdminActions.review_id == review.review_id,
            AdminActions.action_type == AdminActionType.REVIEW_CLOSE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id is None  # System action
        assert action.details["automatic"] is True
        assert action.details["reason"] == "quorum_reached"

    async def test_extend_creates_audit_log(self, db_session: AsyncSession):
        """Extending a review creates an admin_action entry."""
        from sqlalchemy import select

        image = await create_test_image(db_session)
        review = await create_test_review(
            db_session, image.image_id, extension_used=0
        )
        await add_votes(db_session, review.review_id, keep_count=1, remove_count=0)  # type: ignore[arg-type]

        await check_review_deadlines(db_session)

        # Check audit log
        stmt = select(AdminActions).where(
            AdminActions.review_id == review.review_id,
            AdminActions.action_type == AdminActionType.REVIEW_EXTEND,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id is None
        assert action.details["automatic"] is True
        assert action.details["reason"] == "deadline_expired_auto_extend"


@pytest.mark.asyncio
class TestPruneAdminActions:
    """Tests for admin_actions pruning."""

    async def test_prune_old_actions(self, db_session: AsyncSession):
        """Old admin_actions are deleted."""
        # Use timezone-naive datetimes for MySQL compatibility
        now = datetime.now(UTC).replace(tzinfo=None)

        # Create old action (3 years ago)
        old_action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
            created_at=now - timedelta(days=3 * 365),
        )
        db_session.add(old_action)

        # Create recent action (1 month ago)
        recent_action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
            created_at=now - timedelta(days=30),
        )
        db_session.add(recent_action)
        await db_session.commit()
        await db_session.refresh(recent_action)

        deleted = await prune_admin_actions(db_session, retention_years=2)

        assert deleted == 1

        # Verify recent action still exists
        from sqlalchemy import select
        stmt = select(AdminActions).where(AdminActions.action_id == recent_action.action_id)
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is not None

    async def test_prune_no_old_actions(self, db_session: AsyncSession):
        """No actions deleted when all are within retention period."""
        now = datetime.now(UTC).replace(tzinfo=None)
        action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
            created_at=now - timedelta(days=30),
        )
        db_session.add(action)
        await db_session.commit()

        deleted = await prune_admin_actions(db_session, retention_years=2)

        assert deleted == 0


@pytest.mark.asyncio
class TestHelperFunctions:
    """Tests for helper functions."""

    async def test_get_vote_counts(self, db_session: AsyncSession):
        """Vote counts are correctly calculated."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        await add_votes(db_session, review.review_id, keep_count=2, remove_count=3)  # type: ignore[arg-type]

        counts = await _get_vote_counts(db_session, review.review_id)  # type: ignore[arg-type]

        assert counts.get(1, 0) == 2  # keep
        assert counts.get(0, 0) == 3  # remove

    async def test_get_vote_counts_empty(self, db_session: AsyncSession):
        """Empty vote counts returns empty dict."""
        image = await create_test_image(db_session)
        review = await create_test_review(db_session, image.image_id)
        # No votes

        counts = await _get_vote_counts(db_session, review.review_id)  # type: ignore[arg-type]

        assert counts.get(1, 0) == 0
        assert counts.get(0, 0) == 0

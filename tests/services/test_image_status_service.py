"""Tests for the unified change_image_status service."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    DeactivationReason,
    ImageStatus,
    ReviewOutcome,
    ReviewStatus,
)
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_review import ImageReviews
from app.models.image_status_history import ImageStatusHistory
from app.models.user import Users
from app.services.image_status import change_image_status


async def _mk_image(db: AsyncSession, user_id: int, status: int = ImageStatus.ACTIVE) -> Images:
    img = Images(user_id=user_id, filename="x", ext="jpg", md5_hash="a" * 32, status=status)
    db.add(img)
    await db.commit()
    await db.refresh(img)
    return img


async def _mk_open_review(db: AsyncSession, image_id: int, initiated_by: int) -> ImageReviews:
    review = ImageReviews(
        image_id=image_id,
        initiated_by=initiated_by,
        reason_category=DeactivationReason.OTHER,
        status=ReviewStatus.OPEN,
        outcome=ReviewOutcome.PENDING,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review


async def test_deactivate_sets_fields_history_and_audit(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)

    await change_image_status(
        db_session, img, actor,
        new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.SPAM,
        reason="advertising",
    )
    await db_session.commit()
    await db_session.refresh(img)

    assert img.status == ImageStatus.DEACTIVATED
    assert img.reason_category == DeactivationReason.SPAM
    assert img.status_reason == "advertising"
    assert img.status_user_id == actor.user_id

    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert len(hist) == 1
    assert hist[0].new_status == ImageStatus.DEACTIVATED
    assert hist[0].reason_category == DeactivationReason.SPAM
    assert hist[0].reason == "advertising"

    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.image_id == img.image_id)
    )).scalar_one()
    assert action.action_type == AdminActionType.IMAGE_STATUS_CHANGE
    assert action.details["new_status"] == ImageStatus.DEACTIVATED
    assert action.details["reason"] == "advertising"  # free-text reason in the audit row itself


async def test_repost_requires_replacement(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    with pytest.raises(HTTPException) as exc:
        await change_image_status(db_session, img, actor, new_status=ImageStatus.REPOST)
    assert exc.value.status_code == 400


async def test_no_history_row_when_status_unchanged(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    await change_image_status(db_session, img, actor, locked=True)  # lock only
    await db_session.commit()
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert hist == []
    assert img.locked == 1


async def test_system_actor_writes_null_user(db_session: AsyncSession):
    img = await _mk_image(db_session, 1, status=ImageStatus.REVIEW)
    await change_image_status(
        db_session, img, None, new_status=ImageStatus.ACTIVE,
        action_type=AdminActionType.REVIEW_CLOSE, extra_details={"automatic": True},
    )
    await db_session.commit()
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert hist and hist[0].user_id is None  # system action
    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.image_id == img.image_id)
    )).scalar_one()
    assert action.action_type == AdminActionType.REVIEW_CLOSE
    assert action.user_id is None
    assert action.details["automatic"] is True


async def test_report_id_stamped_on_audit_row(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    report = ImageReports(image_id=img.image_id, user_id=actor.user_id, category=2, status=0)
    db_session.add(report)
    await db_session.commit()
    await db_session.refresh(report)

    await change_image_status(
        db_session, img, actor, new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.SPAM, reason="ad",
        action_type=AdminActionType.REPORT_ACTION, report_id=report.report_id,
    )
    await db_session.commit()
    action = (await db_session.execute(
        select(AdminActions).where(AdminActions.report_id == report.report_id)
    )).scalar_one()
    assert action.action_type == AdminActionType.REPORT_ACTION
    assert action.details["reason"] == "ad"


async def test_unhide_requires_reason(db_session: AsyncSession):
    """Un-hiding (hidden -> visible) via a manual mod action must carry a reason."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.DEACTIVATED)
    with pytest.raises(HTTPException) as exc:
        await change_image_status(db_session, img, actor, new_status=ImageStatus.ACTIVE)
    assert exc.value.status_code == 400


async def test_unhide_with_reason_ok(db_session: AsyncSession):
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.DEACTIVATED)
    await change_image_status(
        db_session, img, actor, new_status=ImageStatus.ACTIVE, reason="false positive"
    )
    await db_session.commit()
    await db_session.refresh(img)
    assert img.status == ImageStatus.ACTIVE


async def test_unspoiler_needs_no_reason(db_session: AsyncSession):
    """SPOILER -> ACTIVE is visible -> visible (un-annotate); no reason required."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.SPOILER)
    await change_image_status(db_session, img, actor, new_status=ImageStatus.ACTIVE)
    await db_session.commit()
    await db_session.refresh(img)
    assert img.status == ImageStatus.ACTIVE


async def test_triage_unhide_requires_reason(db_session: AsyncSession):
    """The rule also closes the triage bypass: hidden -> spoiler needs a reason."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.DEACTIVATED)
    with pytest.raises(HTTPException) as exc:
        await change_image_status(
            db_session, img, actor, new_status=ImageStatus.SPOILER,
            action_type=AdminActionType.REPORT_ACTION,
        )
    assert exc.value.status_code == 400


async def test_noop_call_rejected(db_session: AsyncSession):
    """A call with neither new_status nor locked is a no-op and must be rejected
    (it would otherwise write a phantom audit row with no actual change)."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    with pytest.raises(ValueError):
        await change_image_status(db_session, img, actor)


async def test_status_history_row_records_report_id(db_session: AsyncSession):
    """The originating report_id is persisted on the public status-history row."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    report = ImageReports(image_id=img.image_id, user_id=actor.user_id, category=2, status=0)
    db_session.add(report)
    await db_session.commit()
    await db_session.refresh(report)
    await change_image_status(
        db_session, img, actor, new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.INAPPROPRIATE, reason="x",
        action_type=AdminActionType.REPORT_ACTION, report_id=report.report_id,
    )
    await db_session.commit()
    hist = (await db_session.execute(
        select(ImageStatusHistory).where(ImageStatusHistory.image_id == img.image_id)
    )).scalars().all()
    assert hist[0].report_id == report.report_id
    assert hist[0].review_id is None


async def test_status_change_syncs_ml_suggestions(db_session: AsyncSession):
    """Deactivating an image deletes its pending ML suggestions (ADR-0002)."""
    from app.models.ml_tag_suggestion import MlTagSuggestions
    from app.models.tag import Tags

    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    tag = Tags(title="lifecycle-hook-tag", type=1)
    db_session.add(tag)
    await db_session.flush()
    db_session.add(
        MlTagSuggestions(
            image_id=img.image_id,
            tag_id=tag.tag_id,
            confidence=0.9,
            model_version="test-model",
            status="pending",
        )
    )
    await db_session.commit()

    await change_image_status(
        db_session,
        img,
        actor,
        new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.OTHER,
        reason="lifecycle test",
    )
    await db_session.commit()

    remaining = (
        await db_session.execute(
            select(MlTagSuggestions).where(MlTagSuggestions.image_id == img.image_id)
        )
    ).scalars().all()
    assert remaining == []


async def test_manual_status_change_supersedes_open_review(db_session: AsyncSession):
    """A mod deciding the image's fate by hand closes the open review.

    Otherwise the review keeps collecting votes on a decision already made, and
    check_review_deadlines eventually re-applies its own outcome — a KEEP close
    silently reactivating the image the mod just deactivated.
    """
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.REVIEW)
    review = await _mk_open_review(db_session, img.image_id, actor.user_id)

    await change_image_status(
        db_session,
        img,
        actor,
        new_status=ImageStatus.DEACTIVATED,
        reason_category=DeactivationReason.OTHER,
        reason="low quality edit",
    )
    await db_session.commit()
    await db_session.refresh(review)

    assert review.status == ReviewStatus.CLOSED
    assert review.outcome == ReviewOutcome.SUPERSEDED
    assert review.closed_by == actor.user_id
    assert review.closed_at is not None

    close_action = (
        await db_session.execute(
            select(AdminActions).where(
                AdminActions.review_id == review.review_id,
                AdminActions.action_type == AdminActionType.REVIEW_CLOSE,
            )
        )
    ).scalar_one()
    assert close_action.details["outcome"] == ReviewOutcome.SUPERSEDED
    assert close_action.details["new_status"] == ImageStatus.DEACTIVATED


async def test_supersede_fires_on_any_manual_status_change(db_session: AsyncSession):
    """Not just deactivation — restoring the image by hand also settles the review."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.REVIEW)
    review = await _mk_open_review(db_session, img.image_id, actor.user_id)

    await change_image_status(
        db_session, img, actor, new_status=ImageStatus.ACTIVE, reason="looks fine after all"
    )
    await db_session.commit()
    await db_session.refresh(review)

    assert review.status == ReviewStatus.CLOSED
    assert review.outcome == ReviewOutcome.SUPERSEDED


async def test_review_start_does_not_supersede_its_own_review(db_session: AsyncSession):
    """Opening a review sets the image to REVIEW through this same service —
    that status change must not close the review it was made for."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id)
    review = await _mk_open_review(db_session, img.image_id, actor.user_id)

    await change_image_status(
        db_session,
        img,
        actor,
        new_status=ImageStatus.REVIEW,
        action_type=AdminActionType.REVIEW_START,
        review_id=review.review_id,
    )
    await db_session.commit()
    await db_session.refresh(review)

    assert review.status == ReviewStatus.OPEN
    assert review.outcome == ReviewOutcome.PENDING


async def test_review_close_keeps_its_own_outcome(db_session: AsyncSession):
    """The deadline job closes the review itself, then applies the outcome through
    this service. The hook must not overwrite that outcome with SUPERSEDED."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.REVIEW)
    review = await _mk_open_review(db_session, img.image_id, actor.user_id)
    # Deliberately unflushed, mirroring _close_review: the session is
    # autoflush=False, so a SELECT here still sees status=OPEN in the database.
    # Only the review_id guard keeps the hook off this path.
    review.status = ReviewStatus.CLOSED
    review.outcome = ReviewOutcome.KEEP

    await change_image_status(
        db_session,
        img,
        None,
        new_status=ImageStatus.ACTIVE,
        action_type=AdminActionType.REVIEW_CLOSE,
        review_id=review.review_id,
    )
    await db_session.commit()
    await db_session.refresh(review)

    assert review.outcome == ReviewOutcome.KEEP


async def test_lock_only_change_leaves_review_open(db_session: AsyncSession):
    """Locking an image is not a verdict on it — the review keeps running."""
    actor = (await db_session.execute(select(Users).where(Users.user_id == 1))).scalar_one()
    img = await _mk_image(db_session, actor.user_id, status=ImageStatus.REVIEW)
    review = await _mk_open_review(db_session, img.image_id, actor.user_id)

    await change_image_status(db_session, img, actor, locked=True)
    await db_session.commit()
    await db_session.refresh(review)

    assert review.status == ReviewStatus.OPEN

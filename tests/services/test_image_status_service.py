"""Tests for the unified change_image_status service."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, DeactivationReason, ImageStatus
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_status_history import ImageStatusHistory
from app.models.user import Users
from app.services.image_status import change_image_status


async def _mk_image(db: AsyncSession, user_id: int, status: int = ImageStatus.ACTIVE) -> Images:
    img = Images(user_id=user_id, filename="x", ext="jpg", md5_hash="a" * 32, status=status)
    db.add(img)
    await db.commit()
    await db.refresh(img)
    return img


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

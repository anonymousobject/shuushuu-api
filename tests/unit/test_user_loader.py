"""Tests for user loader utilities."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_loader import USER_WITH_GROUPS_OPTIONS, image_uploader_load
from app.models.image import Images
from app.models.permissions import Groups, UserGroups
from app.models.user import Users
from app.schemas.common import UserSummary


@pytest.mark.asyncio
async def test_user_with_groups_options(db_session: AsyncSession):
    """USER_WITH_GROUPS_OPTIONS should load users with groups."""
    from sqlalchemy import select

    # Create a group and add user 1 to it
    group = Groups(title="testers", desc="Testers")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    # Query with the standard options
    result = await db_session.execute(
        select(Users).options(*USER_WITH_GROUPS_OPTIONS).where(Users.user_id == 1)
    )
    user = result.scalar_one()

    assert user.groups == ["testers"]


@pytest.mark.asyncio
async def test_user_summary_auto_populates_groups(db_session: AsyncSession):
    """UserSummary.model_validate should auto-populate groups from User.groups property."""
    from sqlalchemy import select

    # Create a group and add user 1 to it
    group = Groups(title="auto_test", desc="Auto test group")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    # Query with eager loading
    result = await db_session.execute(
        select(Users).options(*USER_WITH_GROUPS_OPTIONS).where(Users.user_id == 1)
    )
    user = result.scalar_one()

    # Create UserSummary - should auto-populate groups
    summary = UserSummary.model_validate(user)

    assert summary.groups == ["auto_test"]


@pytest.mark.asyncio
async def test_image_uploader_load_populates_groups(db_session: AsyncSession):
    """image_uploader_load() must eager-load the uploader's groups.

    Every endpoint that serialises an image's uploader as a UserSummary relies
    on this helper; if it omits user_groups the summary's groups come back empty
    and the frontend can't colour admin/mod/tagger usernames (the /ml-suggestions
    hover-popup regression).
    """
    from sqlalchemy import select

    # Put the fixture user (user 1) in the admins group.
    group = Groups(title="admins", desc="Administrators")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))

    image = Images(
        filename="uploader-load-groups",
        ext="jpg",
        md5_hash="uploaderloadgroupshash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()

    result = await db_session.execute(
        select(Images).options(image_uploader_load()).where(Images.image_id == image.image_id)
    )
    loaded = result.scalar_one()

    # The uploader summary must carry the group so username colouring works.
    summary = UserSummary.model_validate(loaded.user)
    assert summary.groups == ["admins"]


@pytest.mark.asyncio
async def test_user_summary_empty_groups_when_not_loaded(db_session: AsyncSession):
    """UserSummary should have empty groups when user_groups not eager loaded."""
    from sqlalchemy import select

    # Query WITHOUT eager loading
    result = await db_session.execute(select(Users).where(Users.user_id == 1))
    user = result.scalar_one()

    # Create UserSummary - should have empty groups (not raise error)
    summary = UserSummary.model_validate(user)

    assert summary.groups == []

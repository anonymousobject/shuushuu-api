"""Tests for UserGroups relationship to Groups."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.permissions import Groups, UserGroups
from app.models.user import Users


@pytest.mark.asyncio
async def test_user_groups_has_group_relationship(db_session: AsyncSession):
    """UserGroups should have a group relationship that loads the Group."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Create user-group link
    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)
    await db_session.commit()

    # Query with eager loading
    result = await db_session.execute(
        select(UserGroups)
        .options(selectinload(UserGroups.group))
        .where(UserGroups.user_id == 1)
    )
    ug = result.scalar_one()

    assert ug.group is not None
    assert ug.group.title == "mods"


@pytest.mark.asyncio
async def test_users_has_groups_property(db_session: AsyncSession):
    """Users should have a groups property returning group names."""
    # Create groups
    mods = Groups(title="mods", desc="Moderators")
    admins = Groups(title="admins", desc="Administrators")
    db_session.add(mods)
    db_session.add(admins)
    await db_session.flush()

    # Add user 1 to both groups
    db_session.add(UserGroups(user_id=1, group_id=mods.group_id))
    db_session.add(UserGroups(user_id=1, group_id=admins.group_id))
    await db_session.commit()

    # Query user with eager loading
    result = await db_session.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)
        )
        .where(Users.user_id == 1)
    )
    user = result.scalar_one()

    # Check groups property
    assert hasattr(user, "groups")
    assert sorted(user.groups) == ["admins", "mods"]


@pytest.mark.asyncio
async def test_users_groups_property_empty(db_session: AsyncSession):
    """Users with no groups should return empty list."""
    # User 1 exists from fixture but has no groups
    result = await db_session.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)
        )
        .where(Users.user_id == 1)
    )
    user = result.scalar_one()

    assert user.groups == []

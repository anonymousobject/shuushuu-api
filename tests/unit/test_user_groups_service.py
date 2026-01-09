"""Tests for user groups service."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import Groups, UserGroups
from app.services.user_groups import get_groups_for_users


@pytest.mark.asyncio
async def test_get_groups_for_users_empty_list(db_session: AsyncSession):
    """Empty user_ids list returns empty dict."""
    result = await get_groups_for_users(db_session, [])
    assert result == {}


@pytest.mark.asyncio
async def test_get_groups_for_users_no_groups(db_session: AsyncSession):
    """Users with no groups don't appear in result."""
    # User 1 exists from db_session fixture but has no groups
    result = await get_groups_for_users(db_session, [1])
    assert result == {}


@pytest.mark.asyncio
async def test_get_groups_for_users_with_groups(db_session: AsyncSession):
    """Users with groups return their group names."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Add user 1 to the group
    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)
    await db_session.commit()

    result = await get_groups_for_users(db_session, [1])
    assert result == {1: ["mods"]}


@pytest.mark.asyncio
async def test_get_groups_for_users_multiple_groups(db_session: AsyncSession):
    """User with multiple groups returns all group names."""
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

    result = await get_groups_for_users(db_session, [1])
    assert 1 in result
    assert sorted(result[1]) == ["admins", "mods"]


@pytest.mark.asyncio
async def test_get_groups_for_users_mixed(db_session: AsyncSession):
    """Mix of users with and without groups."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Only user 1 gets the group, user 2 has no groups
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    result = await get_groups_for_users(db_session, [1, 2])
    assert result == {1: ["mods"]}
    assert 2 not in result  # User with no groups not in result

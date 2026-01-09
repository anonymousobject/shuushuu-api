"""Tests for user loader utilities."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_loader import USER_WITH_GROUPS_OPTIONS
from app.models.permissions import Groups, UserGroups
from app.models.user import Users


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

"""Tests for UserGroups relationship to Groups."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.permissions import Groups, UserGroups


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

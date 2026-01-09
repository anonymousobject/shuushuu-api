"""User groups service for fetching group memberships."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import Groups, UserGroups


async def get_groups_for_users(db: AsyncSession, user_ids: list[int]) -> dict[int, list[str]]:
    """
    Fetch group names for multiple users in a single query.

    Args:
        db: Database session
        user_ids: List of user IDs to fetch groups for

    Returns:
        Dict mapping user_id to list of group names.
        Users with no groups will not appear in the result.
        Caller should use .get(user_id, []) to handle missing users.
    """
    if not user_ids:
        return {}

    query = (
        select(UserGroups.user_id, Groups.title)  # type: ignore[call-overload]
        .join(Groups, UserGroups.group_id == Groups.group_id)
        .where(UserGroups.user_id.in_(user_ids))  # type: ignore[union-attr]
    )
    result = await db.execute(query)

    groups_by_user: dict[int, list[str]] = {}
    for user_id, group_title in result.fetchall():
        if group_title:  # Skip null titles
            groups_by_user.setdefault(user_id, []).append(group_title)

    return groups_by_user

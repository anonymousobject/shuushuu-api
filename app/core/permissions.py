"""
Permission resolution system for user authorization.

This module provides:
- Permission constants (enum) for type-safe permission references
- Permission resolution from database (groups + user overrides)
- Query utilities for checking user permissions

The permission system uses the existing database schema:
- Users can have permissions through groups (user_groups → group_perms)
- Users can have direct permission overrides (user_perms)
- All permissions are resolved in a single query for efficiency
"""

from enum import Enum

from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import GroupPerms, Perms, UserGroups, UserPerms


class Permission(str, Enum):
    """
    Type-safe permission constants mapped to database perm titles.

    These match the 'title' field in the perms table.
    Using an enum provides:
    - IDE autocomplete
    - Type safety (catch typos at development time)
    - Centralized permission name management
    """

    # Tag management
    TAG_CREATE = "tag_create"  # Create new tags
    TAG_EDIT = "tag_edit"  # Edit existing tags
    TAG_UPDATE = "tag_update"  # Update tag information
    TAG_DELETE = "tag_delete"  # Delete tags

    # Image management
    IMAGE_EDIT_META = "image_edit_meta"
    IMAGE_EDIT = "image_edit"  # Deactivate, delete images
    IMAGE_MARK_REPOST = "image_mark_repost"  # Mark images as reposts
    IMAGE_TAG_ADD = "image_tag_add"  # Add tags to images
    IMAGE_TAG_REMOVE = "image_tag_remove"  # Remove tags from images

    # User/Group management
    GROUP_MANAGE = "group_manage"  # Add, edit groups
    GROUP_PERM_MANAGE = "group_perm_manage"  # Add, edit group permissions
    USER_EDIT_PROFILE = "user_edit_profile"  # Edit user profiles
    USER_BAN = "user_ban"  # Ban users/IPs
    PRIVMSG_VIEW = "privmsg_view"  # View private messages

    # Content moderation
    POST_EDIT = "post_edit"  # Edit text posts (comments)

    # Special permissions
    THEME_EDIT = "theme_edit"  # Theme editor/scheduler access
    RATING_REVOKE = "rating_revoke"  # Revoke image rating rights
    REPORT_REVOKE = "report_revoke"  # Revoke image reporting rights

    # Report & Review system
    REPORT_VIEW = "report_view"  # View report triage queue
    REPORT_MANAGE = "report_manage"  # Dismiss/action/escalate reports
    REVIEW_VIEW = "review_view"  # View open reviews
    REVIEW_START = "review_start"  # Initiate appropriateness review
    REVIEW_VOTE = "review_vote"  # Cast votes on reviews
    REVIEW_CLOSE_EARLY = "review_close_early"  # Close review before deadline


async def get_user_permissions(db: AsyncSession, user_id: int) -> set[str]:
    """
    Resolve all effective permissions for a user.

    Combines permissions from:
    1. Groups the user belongs to (via user_groups → group_perms)
    2. Direct user permission assignments (via user_perms)

    The query uses UNION ALL to combine both sources efficiently.

    Args:
        db: Database session
        user_id: User ID to resolve permissions for

    Returns:
        Set of permission title strings (e.g., {"editimg", "createtag"})
        Empty set if user has no permissions

    Example:
        permissions = await get_user_permissions(db, user_id=123)
        if "editimg" in permissions:
            # User can edit images
    """
    # Query 1: Permissions from groups
    # Join: user_groups → group_perms → perms
    group_perms_query = (
        select(Perms.title)  # type: ignore[call-overload]
        .select_from(UserGroups)
        .join(GroupPerms, UserGroups.group_id == GroupPerms.group_id)
        .join(Perms, GroupPerms.perm_id == Perms.perm_id)
        .where(UserGroups.user_id == user_id)
        .where(GroupPerms.permvalue == 1)  # Only active permissions
    )

    # Query 2: Direct user permissions
    # Join: user_perms → perms
    user_perms_query = (
        select(Perms.title)  # type: ignore[call-overload]
        .select_from(UserPerms)
        .join(Perms, UserPerms.perm_id == Perms.perm_id)
        .where(UserPerms.user_id == user_id)
        .where(UserPerms.permvalue == 1)  # Only active permissions
    )

    # Combine both queries
    combined_query = union_all(group_perms_query, user_perms_query)

    # Execute and collect unique permission titles
    result = await db.execute(combined_query)
    permissions = {row[0] for row in result.fetchall()}

    return permissions


async def has_permission(db: AsyncSession, user_id: int, permission: str | Permission) -> bool:
    """
    Check if a user has a specific permission.

    Args:
        db: Database session
        user_id: User ID to check
        permission: Permission to check (string or Permission enum)

    Returns:
        True if user has the permission, False otherwise

    Example:
        if await has_permission(db, user_id, Permission.EDIT_IMG):
            # User can edit images
    """
    # Convert enum to string if needed
    perm_name = permission.value if isinstance(permission, Permission) else permission

    permissions = await get_user_permissions(db, user_id)
    return perm_name in permissions


async def has_any_permission(
    db: AsyncSession, user_id: int, permissions: list[str | Permission]
) -> bool:
    """
    Check if a user has ANY of the specified permissions.

    Args:
        db: Database session
        user_id: User ID to check
        permissions: List of permissions to check

    Returns:
        True if user has at least one of the permissions, False otherwise

    Example:
        if await has_any_permission(db, user_id, [Permission.EDIT_IMG, Permission.ADMIN_LEVEL]):
            # User can edit images OR is an admin
    """
    # Convert enums to strings
    perm_names = {p.value if isinstance(p, Permission) else p for p in permissions}

    user_permissions = await get_user_permissions(db, user_id)
    return bool(user_permissions & perm_names)


async def has_all_permissions(
    db: AsyncSession, user_id: int, permissions: list[str | Permission]
) -> bool:
    """
    Check if a user has ALL of the specified permissions.

    Args:
        db: Database session
        user_id: User ID to check
        permissions: List of permissions to check

    Returns:
        True if user has all of the permissions, False otherwise

    Example:
        if await has_all_permissions(db, user_id, [Permission.EDIT_IMG, Permission.MOD_LEVEL]):
            # User can edit images AND is a moderator
    """
    # Convert enums to strings
    perm_names = {p.value if isinstance(p, Permission) else p for p in permissions}

    user_permissions = await get_user_permissions(db, user_id)
    return perm_names.issubset(user_permissions)

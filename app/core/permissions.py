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

Note: Permission checking functions accept an optional Redis client for caching.
When provided, permissions are cached in Redis for better performance.
"""

from enum import Enum

import redis.asyncio as redis
from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import GroupPerms, Perms, UserGroups, UserPerms


class Permission(str, Enum):
    """
    Type-safe permission constants mapped to database perm titles.

    The enum is the source of truth for permissions. The database is seeded
    from this enum on startup via sync_permissions().

    Using an enum provides:
    - IDE autocomplete
    - Type safety (catch typos at development time)
    - Centralized permission name management
    - Human-readable descriptions via the description property
    """

    # Tag management
    TAG_CREATE = "tag_create"
    TAG_EDIT = "tag_edit"
    TAG_UPDATE = "tag_update"
    TAG_DELETE = "tag_delete"

    # Image management
    IMAGE_EDIT_META = "image_edit_meta"
    IMAGE_EDIT = "image_edit"
    IMAGE_DELETE = "image_delete"
    IMAGE_MARK_REPOST = "image_mark_repost"
    IMAGE_TAG_ADD = "image_tag_add"
    IMAGE_TAG_REMOVE = "image_tag_remove"

    # User/Group management
    GROUP_MANAGE = "group_manage"
    GROUP_PERM_MANAGE = "group_perm_manage"
    USER_EDIT_PROFILE = "user_edit_profile"
    USER_BAN = "user_ban"
    PRIVMSG_VIEW = "privmsg_view"

    # Content moderation
    POST_EDIT = "post_edit"

    # Special permissions
    THEME_EDIT = "theme_edit"
    RATING_REVOKE = "rating_revoke"
    REPORT_REVOKE = "report_revoke"

    # Report & Review system
    REPORT_VIEW = "report_view"
    REPORT_MANAGE = "report_manage"
    REVIEW_VIEW = "review_view"
    REVIEW_START = "review_start"
    REVIEW_VOTE = "review_vote"
    REVIEW_CLOSE_EARLY = "review_close_early"

    @property
    def description(self) -> str:
        """Human-readable description for this permission."""
        return _PERMISSION_DESCRIPTIONS.get(self, "")


# Module-level constant to avoid recreating dict on each property access
_PERMISSION_DESCRIPTIONS: dict["Permission", str] = {
    # Tag management
    Permission.TAG_CREATE: "Create new tags",
    Permission.TAG_EDIT: "Edit existing tags",
    Permission.TAG_UPDATE: "Update tag information",
    Permission.TAG_DELETE: "Delete tags",
    # Image management
    Permission.IMAGE_EDIT_META: "Edit image metadata",
    Permission.IMAGE_EDIT: "Deactivate images (soft delete, reversible)",
    Permission.IMAGE_DELETE: "Permanently delete images from database and disk",
    Permission.IMAGE_MARK_REPOST: "Mark images as reposts",
    Permission.IMAGE_TAG_ADD: "Add tags to images",
    Permission.IMAGE_TAG_REMOVE: "Remove tags from images",
    # User/Group management
    Permission.GROUP_MANAGE: "Add and edit groups",
    Permission.GROUP_PERM_MANAGE: "Add and edit group permissions",
    Permission.USER_EDIT_PROFILE: "Edit user profiles",
    Permission.USER_BAN: "Ban users and IPs",
    Permission.PRIVMSG_VIEW: "View private messages",
    # Content moderation
    Permission.POST_EDIT: "Edit text posts and comments",
    # Special permissions
    Permission.THEME_EDIT: "Theme editor and scheduler access",
    Permission.RATING_REVOKE: "Revoke image rating rights",
    Permission.REPORT_REVOKE: "Revoke image reporting rights",
    # Report & Review system
    Permission.REPORT_VIEW: "View report triage queue",
    Permission.REPORT_MANAGE: "Dismiss, action, or escalate reports",
    Permission.REVIEW_VIEW: "View open reviews",
    Permission.REVIEW_START: "Initiate appropriateness review",
    Permission.REVIEW_VOTE: "Cast votes on reviews",
    Permission.REVIEW_CLOSE_EARLY: "Close review before deadline",
}


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


async def has_permission(
    db: AsyncSession,
    user_id: int,
    permission: str | Permission,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> bool:
    """
    Check if a user has a specific permission.

    Args:
        db: Database session
        user_id: User ID to check
        permission: Permission to check (string or Permission enum)
        redis_client: Optional Redis client for caching (recommended for performance)

    Returns:
        True if user has the permission, False otherwise

    Example:
        Basic usage without caching:

        ```python
        from app.core.permissions import has_permission, Permission

        if await has_permission(db, user_id, Permission.IMAGE_EDIT_META):
            # User can edit image metadata
            pass
        ```

        Usage within a FastAPI route with Redis caching:

        ```python
        from fastapi import APIRouter, Depends, HTTPException
        from sqlalchemy.ext.asyncio import AsyncSession
        import redis.asyncio as redis

        from app.core.permissions import has_permission, Permission
        from app.core.database import get_db
        from app.core.redis import get_redis
        from app.core.auth import get_current_user

        router = APIRouter()


        @router.get("/images/{image_id}")
        async def read_image(
            image_id: int,
            db: AsyncSession = Depends(get_db),
            redis_client: redis.Redis = Depends(get_redis),
            current_user=Depends(get_current_user),
        ):
            has_perm = await has_permission(
                db,
                user_id=current_user.id,
                permission=Permission.IMAGE_EDIT_META,
                redis_client=redis_client,
            )
            if not has_perm:
                raise HTTPException(status_code=403, detail="Not enough permissions")

            return {"image_id": image_id}
        ```
    """
    # Convert enum to string if needed
    perm_name = permission.value if isinstance(permission, Permission) else permission

    # Use cached version if Redis client is provided
    if redis_client is not None:
        # Lazy import to avoid circular dependency (permission_cache imports from this module)
        from app.core.permission_cache import get_cached_user_permissions

        permissions = await get_cached_user_permissions(db, redis_client, user_id)
    else:
        permissions = await get_user_permissions(db, user_id)

    return perm_name in permissions


async def has_any_permission(
    db: AsyncSession,
    user_id: int,
    permissions: list[str | Permission],
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> bool:
    """
    Check if a user has ANY of the specified permissions.

    Args:
        db: Database session
        user_id: User ID to check
        permissions: List of permissions to check
        redis_client: Optional Redis client for caching (recommended for performance)

    Returns:
        True if user has at least one of the permissions, False otherwise

    Example:
        Basic usage without caching:

        ```python
        from app.core.permissions import has_any_permission, Permission

        if await has_any_permission(db, user_id, [Permission.IMAGE_EDIT, Permission.TAG_CREATE]):
            # User can edit images OR create tags
            pass
        ```

        Usage within a FastAPI route with Redis caching:

        ```python
        from fastapi import APIRouter, Depends, HTTPException
        from sqlalchemy.ext.asyncio import AsyncSession
        import redis.asyncio as redis

        from app.core.permissions import has_any_permission, Permission
        from app.core.database import get_db
        from app.core.redis import get_redis
        from app.core.auth import get_current_user

        router = APIRouter()


        @router.post("/tags")
        async def create_tag(
            db: AsyncSession = Depends(get_db),
            redis_client: redis.Redis = Depends(get_redis),
            current_user=Depends(get_current_user),
        ):
            has_perm = await has_any_permission(
                db,
                user_id=current_user.id,
                permissions=[Permission.TAG_CREATE, Permission.TAG_EDIT],
                redis_client=redis_client,
            )
            if not has_perm:
                raise HTTPException(status_code=403, detail="Not enough permissions")

            return {"status": "ok"}
        ```
    """
    # Convert enums to strings
    perm_names = {p.value if isinstance(p, Permission) else p for p in permissions}

    # Use cached version if Redis client is provided
    if redis_client is not None:
        # Lazy import to avoid circular dependency (permission_cache imports from this module)
        from app.core.permission_cache import get_cached_user_permissions

        user_permissions = await get_cached_user_permissions(db, redis_client, user_id)
    else:
        user_permissions = await get_user_permissions(db, user_id)

    return bool(user_permissions & perm_names)


async def has_all_permissions(
    db: AsyncSession,
    user_id: int,
    permissions: list[str | Permission],
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> bool:
    """
    Check if a user has ALL of the specified permissions.

    Args:
        db: Database session
        user_id: User ID to check
        permissions: List of permissions to check
        redis_client: Optional Redis client for caching (recommended for performance)

    Returns:
        True if user has all of the permissions, False otherwise

    Example:
        Basic usage without caching:

        ```python
        from app.core.permissions import has_all_permissions, Permission

        if await has_all_permissions(db, user_id, [Permission.IMAGE_EDIT, Permission.TAG_CREATE]):
            # User can BOTH edit images AND create tags
            pass
        ```

        Usage within a FastAPI route with Redis caching:

        ```python
        from fastapi import APIRouter, Depends, HTTPException
        from sqlalchemy.ext.asyncio import AsyncSession
        import redis.asyncio as redis

        from app.core.permissions import has_all_permissions, Permission
        from app.core.database import get_db
        from app.core.redis import get_redis
        from app.core.auth import get_current_user

        router = APIRouter()


        @router.post("/admin/groups")
        async def manage_group(
            db: AsyncSession = Depends(get_db),
            redis_client: redis.Redis = Depends(get_redis),
            current_user=Depends(get_current_user),
        ):
            has_perm = await has_all_permissions(
                db,
                user_id=current_user.id,
                permissions=[Permission.GROUP_MANAGE, Permission.GROUP_PERM_MANAGE],
                redis_client=redis_client,
            )
            if not has_perm:
                raise HTTPException(status_code=403, detail="Not enough permissions")

            return {"status": "ok"}
        ```
    """
    # Convert enums to strings
    perm_names = {p.value if isinstance(p, Permission) else p for p in permissions}

    # Use cached version if Redis client is provided
    if redis_client is not None:
        # Lazy import to avoid circular dependency (permission_cache imports from this module)
        from app.core.permission_cache import get_cached_user_permissions

        user_permissions = await get_cached_user_permissions(db, redis_client, user_id)
    else:
        user_permissions = await get_user_permissions(db, user_id)

    return perm_names.issubset(user_permissions)

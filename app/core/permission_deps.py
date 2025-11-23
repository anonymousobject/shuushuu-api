"""
FastAPI dependencies for permission-based route protection.

This module provides dependency functions for requiring specific permissions
on routes. These dependencies integrate with the existing auth system and
raise appropriate HTTP exceptions when permissions are missing.

Usage:
    from app.core.permission_deps import require_permission
    from app.core.permissions import Permission

    @router.delete("/images/{id}")
    async def delete_image(
        user: CurrentUser,
        _: Annotated[None, Depends(require_permission(Permission.IMAGE_EDIT))]
    ):
        # This code only runs if user has image_edit permission
        ...
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, has_any_permission, has_permission
from app.models.user import Users


def require_permission(
    permission: str | Permission,
) -> Callable[[Users, AsyncSession], Coroutine[Any, Any, None]]:
    """
    Create a FastAPI dependency that requires a specific permission.

    Returns a dependency function that:
    1. Gets the current authenticated user
    2. Checks if they have the required permission
    3. Raises 403 if permission is missing
    4. Returns None if permission is present (use with _ to discard)

    Args:
        permission: Permission required (string or Permission enum)

    Returns:
        FastAPI dependency function

    Example:
        @router.delete("/images/{id}")
        async def delete_image(
            user: CurrentUser,
            _: Annotated[None, Depends(require_permission(Permission.IMAGE_EDIT))]
        ):
            # User definitely has image_edit permission here
            ...

    Raises:
        HTTPException: 403 Forbidden if user lacks permission
    """

    async def permission_checker(
        user: Annotated[Users, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        # Convert enum to string for error message
        perm_name = permission.value if isinstance(permission, Permission) else permission

        # Check permission
        if not await has_permission(db, user.user_id, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Requires permission: {perm_name}",
            )

    return permission_checker


def require_any_permission(
    permissions: list[str | Permission],
) -> Callable[[Users, AsyncSession], Coroutine[Any, Any, None]]:
    """
    Create a FastAPI dependency that requires ANY of the specified permissions.

    Useful for routes that can be accessed by multiple permission types.

    Args:
        permissions: List of acceptable permissions (strings or Permission enums)

    Returns:
        FastAPI dependency function

    Example:
        @router.post("/images/{id}/tag")
        async def tag_image(
            user: CurrentUser,
            _: Annotated[None, Depends(require_any_permission([
                Permission.TAG_CREATE,
                Permission.LEVEL_TAGGER,
                Permission.LEVEL_MOD
            ]))]
        ):
            # User has at least one of the listed permissions
            ...

    Raises:
        HTTPException: 403 Forbidden if user lacks all permissions
    """

    async def permission_checker(
        user: Annotated[Users, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        # Check permissions
        if not await has_any_permission(db, user.user_id, permissions):
            # Convert enums to strings for error message
            perm_names = [p.value if isinstance(p, Permission) else p for p in permissions]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Requires one of: {', '.join(perm_names)}",
            )

    return permission_checker


def require_all_permissions(
    permissions: list[str | Permission],
) -> Callable[[Users, AsyncSession], Coroutine[Any, Any, None]]:
    """
    Create a FastAPI dependency that requires ALL of the specified permissions.

    Useful for routes that need multiple permissions to access.

    Args:
        permissions: List of required permissions (strings or Permission enums)

    Returns:
        FastAPI dependency function

    Example:
        @router.post("/groups/{id}/permissions")
        async def modify_group_perms(
            user: CurrentUser,
            _: Annotated[None, Depends(require_all_permissions([
                Permission.GROUP_MANAGE,
                Permission.GROUP_PERM_MANAGE
            ]))]
        ):
            # User has both permissions
            ...

    Raises:
        HTTPException: 403 Forbidden if user lacks any of the permissions
    """

    async def permission_checker(
        user: Annotated[Users, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        # Get user's permissions
        from app.core.permissions import get_user_permissions

        user_perms = await get_user_permissions(db, user.user_id)
        required_set = {p.value if isinstance(p, Permission) else p for p in permissions}

        # Check if user has all required permissions
        missing_perms = required_set - user_perms
        if missing_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Missing: {', '.join(missing_perms)}",
            )

    return permission_checker


# Convenience type aliases for common permission requirements
RequireImageEdit = Annotated[None, Depends(require_permission(Permission.IMAGE_EDIT))]
RequireTagCreate = Annotated[None, Depends(require_permission(Permission.TAG_CREATE))]
RequireTagEdit = Annotated[None, Depends(require_permission(Permission.TAG_EDIT))]
RequireUserBan = Annotated[None, Depends(require_permission(Permission.USER_BAN))]
# RequireModLevel = Annotated[None, Depends(require_permission(Permission.LEVEL_MOD))]
# RequireAdminLevel = Annotated[None, Depends(require_permission(Permission.LEVEL_ADMIN))]

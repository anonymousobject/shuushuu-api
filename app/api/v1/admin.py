"""
Admin API endpoints for managing groups, permissions, user assignments,
the image reporting/review system, and user suspensions.

These endpoints require admin-level permissions and provide:
- Group CRUD operations
- Group membership management (add/remove users from groups)
- Group permission management (add/remove permissions from groups)
- Direct user permission management (add/remove permissions for individual users)
- Permission listing
- Report triage (list, dismiss, action, escalate)
- Review management (list, create, vote, close, extend)
- User suspension management (suspend, reactivate, view history)
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    ImageStatus,
    ReportCategory,
    ReportStatus,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
    SuspensionAction,
    settings,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permission_cache import invalidate_group_permissions, invalidate_user_permissions
from app.core.permission_deps import require_all_permissions, require_permission
from app.core.permissions import Permission
from app.core.redis import get_redis
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_report_tag_suggestion import ImageReportTagSuggestions
from app.models.image_review import ImageReviews
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups, UserPerms
from app.models.refresh_token import RefreshTokens
from app.models.review_vote import ReviewVotes
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.models.user_suspension import UserSuspensions
from app.schemas.admin import (
    GroupCreate,
    GroupListResponse,
    GroupMemberItem,
    GroupMembersResponse,
    GroupPermItem,
    GroupPermsResponse,
    GroupResponse,
    GroupUpdate,
    ImageStatusResponse,
    ImageStatusUpdate,
    MessageResponse,
    PermListResponse,
    PermResponse,
    SuspendUserRequest,
    SuspensionListResponse,
    SuspensionResponse,
    UserGroupItem,
    UserGroupsResponse,
    UserPermItem,
    UserPermsResponse,
)
from app.schemas.report import (
    ApplyTagSuggestionsRequest,
    ApplyTagSuggestionsResponse,
    ReportActionRequest,
    ReportDismissRequest,
    ReportEscalateRequest,
    ReportListResponse,
    ReportResponse,
    ReviewCloseRequest,
    ReviewCreate,
    ReviewDetailResponse,
    ReviewExtendRequest,
    ReviewListResponse,
    ReviewResponse,
    ReviewVoteRequest,
    TagSuggestion,
    VoteResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ===== Group CRUD =====


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    search: Annotated[str | None, Query(description="Search groups by title")] = None,
    db: AsyncSession = Depends(get_db),
) -> GroupListResponse:
    """
    List all groups.

    Requires GROUP_MANAGE permission.
    """
    query = select(Groups)

    if search:
        query = query.where(Groups.title.like(f"%{search}%"))  # type: ignore[union-attr]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Execute
    result = await db.execute(query)
    groups = result.scalars().all()

    return GroupListResponse(
        total=total,
        groups=[GroupResponse.model_validate(g) for g in groups],
    )


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """
    Create a new group.

    Requires GROUP_MANAGE permission.
    """
    # Check if group with same title exists
    existing = await db.execute(select(Groups).where(Groups.title == group_data.title))  # type: ignore[arg-type]
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group with this title already exists")

    new_group = Groups(title=group_data.title, desc=group_data.desc)
    db.add(new_group)
    await db.commit()
    await db.refresh(new_group)

    return GroupResponse.model_validate(new_group)


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: Annotated[int, Path(description="Group ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """
    Get a specific group by ID.

    Requires GROUP_MANAGE permission.
    """
    result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    return GroupResponse.model_validate(group)


@router.put("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: Annotated[int, Path(description="Group ID")],
    group_data: GroupUpdate,
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    """
    Update a group.

    Requires GROUP_MANAGE permission.
    """
    result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Update fields if provided
    if group_data.title is not None:
        group.title = group_data.title
    if group_data.desc is not None:
        group.desc = group_data.desc

    await db.commit()
    await db.refresh(group)

    return GroupResponse.model_validate(group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: Annotated[int, Path(description="Group ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a group.

    This will also remove all users from the group and all permissions assigned to the group.

    Requires GROUP_MANAGE permission.
    """
    result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await db.delete(group)
    await db.commit()


# ===== Group Membership =====


@router.get("/groups/{group_id}/members", response_model=GroupMembersResponse)
async def list_group_members(
    group_id: Annotated[int, Path(description="Group ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> GroupMembersResponse:
    """
    List all members of a group.

    Requires GROUP_MANAGE permission.
    """
    # Verify group exists
    group_result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    group = group_result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Get members
    result = await db.execute(
        select(Users.user_id, Users.username)  # type: ignore[call-overload]
        .join(UserGroups, UserGroups.user_id == Users.user_id)
        .where(UserGroups.group_id == group_id)
    )
    members = result.all()

    return GroupMembersResponse(
        group_id=group_id,
        group_title=group.title,
        total=len(members),
        members=[GroupMemberItem(user_id=m.user_id, username=m.username) for m in members],
    )


@router.post(
    "/groups/{group_id}/members/{user_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_user_to_group(
    group_id: Annotated[int, Path(description="Group ID")],
    user_id: Annotated[int, Path(description="User ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Add a user to a group.

    Requires GROUP_MANAGE permission.
    """
    # Verify group exists
    group_result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    existing = await db.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id,  # type: ignore[arg-type]
            UserGroups.group_id == group_id,  # type: ignore[arg-type]
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member of this group")

    # Add membership
    membership = UserGroups(user_id=user_id, group_id=group_id)
    db.add(membership)
    await db.commit()

    return MessageResponse(message="User added to group successfully")


@router.delete(
    "/groups/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_user_from_group(
    group_id: Annotated[int, Path(description="Group ID")],
    user_id: Annotated[int, Path(description="User ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Remove a user from a group.

    Requires GROUP_MANAGE permission.
    """
    # Check if membership exists
    result = await db.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id,  # type: ignore[arg-type]
            UserGroups.group_id == group_id,  # type: ignore[arg-type]
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="User is not a member of this group")

    await db.delete(membership)
    await db.commit()


# ===== Group Permissions =====


@router.get("/groups/{group_id}/permissions", response_model=GroupPermsResponse)
async def list_group_permissions(
    group_id: Annotated[int, Path(description="Group ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> GroupPermsResponse:
    """
    List all permissions assigned to a group.

    Requires GROUP_PERM_MANAGE permission.
    """
    # Verify group exists
    group_result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    group = group_result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Get permissions
    result = await db.execute(
        select(Perms.perm_id, Perms.title, Perms.desc, GroupPerms.permvalue)  # type: ignore[call-overload]
        .join(GroupPerms, GroupPerms.perm_id == Perms.perm_id)
        .where(GroupPerms.group_id == group_id)
    )
    perms = result.all()

    return GroupPermsResponse(
        group_id=group_id,
        group_title=group.title,
        total=len(perms),
        permissions=[
            GroupPermItem(
                perm_id=p.perm_id,
                title=p.title,
                desc=p.desc,
                permvalue=p.permvalue,
            )
            for p in perms
        ],
    )


@router.post(
    "/groups/{group_id}/permissions/{perm_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_permission_to_group(
    group_id: Annotated[int, Path(description="Group ID")],
    perm_id: Annotated[int, Path(description="Permission ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    permvalue: Annotated[int, Query(description="Permission value (1=enabled, 0=disabled)")] = 1,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> MessageResponse:
    """
    Add a permission to a group.

    Requires GROUP_PERM_MANAGE permission.
    """
    # Verify group exists
    group_result = await db.execute(select(Groups).where(Groups.group_id == group_id))  # type: ignore[arg-type]
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Verify permission exists
    perm_result = await db.execute(select(Perms).where(Perms.perm_id == perm_id))  # type: ignore[arg-type]
    if not perm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Permission not found")

    # Check if already assigned
    existing = await db.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group_id,  # type: ignore[arg-type]
            GroupPerms.perm_id == perm_id,  # type: ignore[arg-type]
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Permission already assigned to this group")

    # Add permission
    group_perm = GroupPerms(group_id=group_id, perm_id=perm_id, permvalue=permvalue)
    db.add(group_perm)
    await db.commit()

    # Invalidate permission cache for all users in this group
    await invalidate_group_permissions(redis_client, db, group_id)

    return MessageResponse(message="Permission added to group successfully")


@router.delete(
    "/groups/{group_id}/permissions/{perm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_permission_from_group(
    group_id: Annotated[int, Path(description="Group ID")],
    perm_id: Annotated[int, Path(description="Permission ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> None:
    """
    Remove a permission from a group.

    Requires GROUP_PERM_MANAGE permission.
    """
    # Check if assignment exists
    result = await db.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group_id,  # type: ignore[arg-type]
            GroupPerms.perm_id == perm_id,  # type: ignore[arg-type]
        )
    )
    group_perm = result.scalar_one_or_none()

    if not group_perm:
        raise HTTPException(status_code=404, detail="Permission not assigned to this group")

    await db.delete(group_perm)
    await db.commit()

    # Invalidate permission cache for all users in this group
    await invalidate_group_permissions(redis_client, db, group_id)


# ===== Direct User Permissions =====


@router.get("/users/{user_id}/permissions", response_model=UserPermsResponse)
async def list_user_permissions(
    user_id: Annotated[int, Path(description="User ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> UserPermsResponse:
    """
    List all direct permissions assigned to a user (not from groups).

    Requires GROUP_PERM_MANAGE permission.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get direct permissions
    result = await db.execute(
        select(Perms.perm_id, Perms.title, Perms.desc, UserPerms.permvalue)  # type: ignore[call-overload]
        .join(UserPerms, UserPerms.perm_id == Perms.perm_id)
        .where(UserPerms.user_id == user_id)
    )
    perms = result.all()

    return UserPermsResponse(
        user_id=user_id,
        username=user.username,
        total=len(perms),
        permissions=[
            UserPermItem(
                perm_id=p.perm_id,
                title=p.title,
                desc=p.desc,
                permvalue=p.permvalue,
            )
            for p in perms
        ],
    )


@router.post(
    "/users/{user_id}/permissions/{perm_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_permission_to_user(
    user_id: Annotated[int, Path(description="User ID")],
    perm_id: Annotated[int, Path(description="Permission ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    permvalue: Annotated[int, Query(description="Permission value (1=enabled, 0=disabled)")] = 1,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> MessageResponse:
    """
    Add a direct permission to a user.

    Requires GROUP_PERM_MANAGE permission.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    # Verify permission exists
    perm_result = await db.execute(select(Perms).where(Perms.perm_id == perm_id))  # type: ignore[arg-type]
    if not perm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Permission not found")

    # Check if already assigned
    existing = await db.execute(
        select(UserPerms).where(
            UserPerms.user_id == user_id,  # type: ignore[arg-type]
            UserPerms.perm_id == perm_id,  # type: ignore[arg-type]
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Permission already assigned to this user")

    # Add permission
    user_perm = UserPerms(user_id=user_id, perm_id=perm_id, permvalue=permvalue)
    db.add(user_perm)
    await db.commit()

    # Invalidate permission cache for this user
    await invalidate_user_permissions(redis_client, user_id)

    return MessageResponse(message="Permission added to user successfully")


@router.delete(
    "/users/{user_id}/permissions/{perm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_permission_from_user(
    user_id: Annotated[int, Path(description="User ID")],
    perm_id: Annotated[int, Path(description="Permission ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> None:
    """
    Remove a direct permission from a user.

    Requires GROUP_PERM_MANAGE permission.
    """
    # Check if assignment exists
    result = await db.execute(
        select(UserPerms).where(
            UserPerms.user_id == user_id,  # type: ignore[arg-type]
            UserPerms.perm_id == perm_id,  # type: ignore[arg-type]
        )
    )
    user_perm = result.scalar_one_or_none()

    if not user_perm:
        raise HTTPException(status_code=404, detail="Permission not assigned to this user")

    await db.delete(user_perm)
    await db.commit()

    # Invalidate permission cache for this user
    await invalidate_user_permissions(redis_client, user_id)


# ===== Permission Listing =====


@router.get("/permissions", response_model=PermListResponse)
async def list_permissions(
    _: Annotated[None, Depends(require_permission(Permission.GROUP_PERM_MANAGE))],
    search: Annotated[str | None, Query(description="Search permissions by title")] = None,
    db: AsyncSession = Depends(get_db),
) -> PermListResponse:
    """
    List all available permissions.

    Requires GROUP_PERM_MANAGE permission.
    """
    query = select(Perms)

    if search:
        query = query.where(Perms.title.like(f"%{search}%"))  # type: ignore[union-attr]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Execute
    result = await db.execute(query)
    perms = result.scalars().all()

    return PermListResponse(
        total=total,
        permissions=[PermResponse.model_validate(p) for p in perms],
    )


# ===== User Groups =====


@router.get("/users/{user_id}/groups", response_model=UserGroupsResponse)
async def list_user_groups(
    user_id: Annotated[int, Path(description="User ID")],
    _: Annotated[None, Depends(require_permission(Permission.GROUP_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> UserGroupsResponse:
    """
    List all groups a user belongs to.

    Requires GROUP_MANAGE permission.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get groups
    result = await db.execute(
        select(Groups.group_id, Groups.title, Groups.desc)  # type: ignore[call-overload]
        .join(UserGroups, UserGroups.group_id == Groups.group_id)
        .where(UserGroups.user_id == user_id)
    )
    groups = result.all()

    return UserGroupsResponse(
        user_id=user_id,
        username=user.username,
        total=len(groups),
        groups=[UserGroupItem(group_id=g.group_id, title=g.title, desc=g.desc) for g in groups],
    )


# ===== Direct Image Moderation =====


@router.patch("/images/{image_id}", response_model=ImageStatusResponse)
async def change_image_status(
    image_id: Annotated[int, Path(description="Image ID")],
    status_data: ImageStatusUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.IMAGE_EDIT))],
    db: AsyncSession = Depends(get_db),
) -> ImageStatusResponse:
    """
    Change an image's status and/or locked state.

    Use this for quick moderation actions without creating a report.
    Can update status, locked, or both in a single request.

    Requires IMAGE_EDIT permission.
    """
    # Get the image
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = image.status
    previous_locked = image.locked

    # Handle status change if provided
    if status_data.status is not None:
        # Handle repost status
        if status_data.status == ImageStatus.REPOST:
            if status_data.replacement_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="replacement_id is required when marking as repost",
                )
            if status_data.replacement_id == image_id:
                raise HTTPException(
                    status_code=400,
                    detail="An image cannot be a repost of itself",
                )
            # Verify original image exists
            original_result = await db.execute(
                select(Images).where(Images.image_id == status_data.replacement_id)  # type: ignore[arg-type]
            )
            if not original_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=404,
                    detail="Original image not found",
                )
            image.replacement_id = status_data.replacement_id
        else:
            # Clear replacement_id when not a repost
            image.replacement_id = None

        # Update image status
        image.status = status_data.status
        image.status_user_id = current_user.user_id
        image.status_updated = datetime.now(UTC)

    # Handle locked change if provided
    if status_data.locked is not None:
        image.locked = 1 if status_data.locked else 0

    # Log admin action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.IMAGE_STATUS_CHANGE,
        image_id=image_id,
        details={
            "previous_status": previous_status,
            "new_status": image.status,
            "previous_locked": previous_locked,
            "new_locked": image.locked,
            "replacement_id": image.replacement_id,
        },
    )
    db.add(action)

    await db.commit()
    await db.refresh(image)

    return ImageStatusResponse.model_validate(image)


# ===== Report Triage =====


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    _: Annotated[None, Depends(require_permission(Permission.REPORT_VIEW))],
    status_filter: Annotated[
        int | None,
        Query(alias="status", description="Filter by status (0=pending, 1=reviewed, 2=dismissed)"),
    ] = ReportStatus.PENDING,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    db: AsyncSession = Depends(get_db),
) -> ReportListResponse:
    """
    List image reports in the triage queue.

    Requires REPORT_VIEW permission.
    """
    # Build a filtered base query against ImageReports (used for counting)
    base_query = select(ImageReports)
    if status_filter is not None:
        # SQLModel/SQLAlchemy equality comparisons sometimes confuse mypy; ignore arg-type here
        base_query = base_query.where(ImageReports.status == status_filter)  # type: ignore[arg-type]

    # Count total rows efficiently using ImageReports only
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Now select ImageReports rows plus the reporting user's username, joining on user_id
    query = select(ImageReports, Users.username)  # type: ignore[call-overload]
    query = query.join(Users, Users.user_id == ImageReports.user_id)
    if status_filter is not None:
        query = query.where(ImageReports.status == status_filter)

    # Apply pagination and ordering (newest first)
    offset = (page - 1) * per_page
    query = query.order_by(desc(ImageReports.created_at))  # type: ignore[arg-type]
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    rows = result.all()

    # Collect report IDs to fetch tag suggestions
    report_ids = [report.report_id for report, _ in rows]

    # Fetch tag suggestions for all reports in one query
    suggestions_by_report: dict[int, list[TagSuggestion]] = {}
    if report_ids:
        suggestions_result = await db.execute(
            select(ImageReportTagSuggestions, Tags)
            .join(Tags, Tags.tag_id == ImageReportTagSuggestions.tag_id)  # type: ignore[arg-type]
            .where(ImageReportTagSuggestions.report_id.in_(report_ids))  # type: ignore[attr-defined]
        )
        for suggestion, tag in suggestions_result.all():
            if suggestion.report_id not in suggestions_by_report:
                suggestions_by_report[suggestion.report_id] = []
            suggestions_by_report[suggestion.report_id].append(
                TagSuggestion(
                    suggestion_id=suggestion.suggestion_id or 0,
                    tag_id=suggestion.tag_id,
                    tag_name=tag.title or "",
                    tag_type=tag.type,
                    accepted=suggestion.accepted,
                )
            )

    items: list[ReportResponse] = []
    for report, username in rows:
        response = ReportResponse.model_validate(report)
        response.username = username
        # Add tag suggestions if present
        if report.report_id in suggestions_by_report:
            response.suggested_tags = suggestions_by_report[report.report_id]
        items.append(response)

    return ReportListResponse(
        total=total,
        page=page,
        per_page=per_page,
        items=items,
    )


@router.post("/reports/{report_id}/dismiss", response_model=MessageResponse)
async def dismiss_report(
    report_id: Annotated[int, Path(description="Report ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    request_data: ReportDismissRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Dismiss a report without taking action on the image.

    Optionally include admin_notes to document why the report was dismissed.

    Requires REPORT_MANAGE permission.
    """
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Mark all tag suggestions as rejected
    suggestions_result = await db.execute(
        select(ImageReportTagSuggestions).where(
            ImageReportTagSuggestions.report_id == report_id  # type: ignore[arg-type]
        )
    )
    for suggestion in suggestions_result.scalars().all():
        suggestion.accepted = False

    # Update report
    report.status = ReportStatus.DISMISSED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data and request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    # Log action
    details = {}
    if request_data and request_data.admin_notes:
        details["admin_notes"] = request_data.admin_notes
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_DISMISS,
        report_id=report_id,
        image_id=report.image_id,
        details=details,
    )
    db.add(action)

    await db.commit()

    return MessageResponse(message="Report dismissed successfully")


@router.post(
    "/reports/{report_id}/apply-tag-suggestions",
    response_model=ApplyTagSuggestionsResponse,
)
async def apply_tag_suggestions(
    report_id: Annotated[int, Path(description="Report ID")],
    request_data: ApplyTagSuggestionsRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> ApplyTagSuggestionsResponse:
    """
    Apply tag suggestions from a TAG_SUGGESTIONS report.

    Approves specified suggestions, rejects others, adds approved tags
    to the image, and marks the report as reviewed.

    Requires REPORT_MANAGE permission.
    """
    # Get the report
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Validate this is a TAG_SUGGESTIONS report
    if report.category != ReportCategory.TAG_SUGGESTIONS:
        raise HTTPException(
            status_code=400, detail="Tag suggestions can only be applied to TAG_SUGGESTIONS reports"
        )

    # Get all suggestions for this report
    suggestions_result = await db.execute(
        select(ImageReportTagSuggestions).where(
            ImageReportTagSuggestions.report_id == report_id  # type: ignore[arg-type]
        )
    )
    suggestions = list(suggestions_result.scalars().all())

    if not suggestions:
        raise HTTPException(status_code=400, detail="This report has no tag suggestions")

    # Validate approved_suggestion_ids belong to this report
    suggestion_ids = {s.suggestion_id for s in suggestions}
    for sid in request_data.approved_suggestion_ids:
        if sid not in suggestion_ids:
            raise HTTPException(status_code=400, detail=f"Invalid suggestion ID: {sid}")

    # Get existing tags on image
    existing_tags_result = await db.execute(
        select(TagLinks.tag_id).where(TagLinks.image_id == report.image_id)  # type: ignore[call-overload]
    )
    existing_tag_ids = set(existing_tags_result.scalars().all())

    approved_ids = set(request_data.approved_suggestion_ids)
    applied_tags: list[int] = []
    removed_tags: list[int] = []
    already_present: list[int] = []
    already_absent: list[int] = []

    for suggestion in suggestions:
        if suggestion.suggestion_id in approved_ids:
            suggestion.accepted = True

            if suggestion.suggestion_type == 1:  # Add
                if suggestion.tag_id not in existing_tag_ids:
                    tag_link = TagLinks(image_id=report.image_id, tag_id=suggestion.tag_id)
                    db.add(tag_link)
                    applied_tags.append(suggestion.tag_id)
                    existing_tag_ids.add(suggestion.tag_id)
                else:
                    already_present.append(suggestion.tag_id)

            elif suggestion.suggestion_type == 2:  # Remove
                if suggestion.tag_id in existing_tag_ids:
                    await db.execute(
                        delete(TagLinks).where(
                            TagLinks.image_id == report.image_id,
                            TagLinks.tag_id == suggestion.tag_id,
                        )
                    )
                    removed_tags.append(suggestion.tag_id)
                    existing_tag_ids.discard(suggestion.tag_id)
                else:
                    already_absent.append(suggestion.tag_id)
        else:
            suggestion.accepted = False

    # Update report
    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)
    if request_data.admin_notes:
        report.admin_notes = request_data.admin_notes

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_ACTION,
        report_id=report_id,
        image_id=report.image_id,
        details={
            "action": "apply_tag_suggestions",
            "approved_count": len(approved_ids),
            "rejected_count": len(suggestions) - len(approved_ids),
            "applied_tags": applied_tags,
            "removed_tags": removed_tags,
        },
    )
    db.add(action)

    await db.commit()

    return ApplyTagSuggestionsResponse(
        message=f"Applied {len(applied_tags)} tags, removed {len(removed_tags)} tags",
        applied_tags=applied_tags,
        removed_tags=removed_tags,
        already_present=already_present,
        already_absent=already_absent,
    )


@router.post("/reports/{report_id}/action", response_model=MessageResponse)
async def action_report(
    report_id: Annotated[int, Path(description="Report ID")],
    action_data: ReportActionRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Take action on a report by changing the image status.

    This marks the report as reviewed and updates the image status.

    Requires REPORT_MANAGE permission.
    """
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Get the image
    image_result = await db.execute(
        select(Images).where(Images.image_id == report.image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = image.status

    # Update image status
    image.status = action_data.new_status
    image.status_user_id = current_user.user_id
    image.status_updated = datetime.now(UTC)

    # Update report
    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_ACTION,
        report_id=report_id,
        image_id=report.image_id,
        details={"previous_status": previous_status, "new_status": action_data.new_status},
    )
    db.add(action)

    await db.commit()

    return MessageResponse(message="Report processed and image status updated")


@router.post("/reports/{report_id}/escalate", response_model=ReviewResponse)
async def escalate_report(
    report_id: Annotated[int, Path(description="Report ID")],
    escalate_data: ReportEscalateRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[
        None, Depends(require_all_permissions([Permission.REPORT_MANAGE, Permission.REVIEW_START]))
    ],
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """
    Escalate a report to a full review (voting process).

    This marks the report as reviewed and creates a new review session.
    The image status is set to REVIEW (hidden from users).

    Requires REPORT_MANAGE and REVIEW_START permissions.
    """
    result = await db.execute(
        select(ImageReports).where(ImageReports.report_id == report_id)  # type: ignore[arg-type]
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status != ReportStatus.PENDING:
        raise HTTPException(status_code=400, detail="Report has already been processed")

    # Check if there's already an open review for this image
    existing_review = await db.execute(
        select(ImageReviews).where(
            ImageReviews.image_id == report.image_id,  # type: ignore[arg-type]
            ImageReviews.status == ReviewStatus.OPEN,  # type: ignore[arg-type]
        )
    )
    if existing_review.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Image already has an open review")

    # Get the image
    image_result = await db.execute(
        select(Images).where(Images.image_id == report.image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Calculate deadline
    deadline_days = escalate_data.deadline_days or settings.REVIEW_DEADLINE_DAYS
    deadline = datetime.now(UTC) + timedelta(days=deadline_days)

    # Update report
    report.status = ReportStatus.REVIEWED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)

    # Set image to review status
    previous_status = image.status
    image.status = ImageStatus.REVIEW
    image.status_user_id = current_user.user_id
    image.status_updated = datetime.now(UTC)

    # Create review
    review = ImageReviews(
        image_id=report.image_id,
        source_report_id=report_id,
        initiated_by=current_user.user_id,
        review_type=ReviewType.APPROPRIATENESS,
        deadline=deadline,
        status=ReviewStatus.OPEN,
        outcome=ReviewOutcome.PENDING,
    )
    db.add(review)
    await db.flush()  # Get review_id

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REVIEW_START,
        report_id=report_id,
        review_id=review.review_id,
        image_id=report.image_id,
        details={"previous_status": previous_status, "deadline_days": deadline_days},
    )
    db.add(action)

    await db.commit()
    await db.refresh(review)

    return ReviewResponse.model_validate(review)


# ===== Reviews (Voting Process) =====


@router.get("/reviews", response_model=ReviewListResponse)
async def list_reviews(
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_VIEW))],
    status_filter: Annotated[
        int | None, Query(alias="status", description="Filter by status (0=open, 1=closed)")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    db: AsyncSession = Depends(get_db),
) -> ReviewListResponse:
    """
    List review sessions.

    Requires REVIEW_VIEW permission.
    """
    query = (
        select(  # type: ignore[call-overload]
            ImageReviews,
            Users.username,
            ImageReports.category,
            ImageReports.reason_text,
        )
        .outerjoin(Users, ImageReviews.initiated_by == Users.user_id)
        .outerjoin(ImageReports, ImageReviews.source_report_id == ImageReports.report_id)
    )

    if status_filter is not None:
        query = query.where(ImageReviews.status == status_filter)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering (newest first)
    offset = (page - 1) * per_page
    query = query.order_by(desc(ImageReviews.created_at)).offset(offset).limit(per_page)  # type: ignore[arg-type]

    result = await db.execute(query)
    rows = result.all()

    # Build responses with vote counts
    items = []
    for review, initiated_by_username, report_category, report_reason in rows:
        # Get vote counts
        vote_result = await db.execute(
            select(
                func.count().label("total"),
                func.sum(ReviewVotes.vote).label("keep_votes"),  # vote=1 is keep
            ).where(ReviewVotes.review_id == review.review_id)
        )
        vote_row = vote_result.one()
        vote_count = vote_row.total or 0
        keep_votes = int(vote_row.keep_votes or 0)
        remove_votes = vote_count - keep_votes

        response = ReviewResponse.model_validate(review)
        response.initiated_by_username = initiated_by_username
        response.source_report_category = report_category
        if report_category is not None:
            response.source_report_category_label = ReportCategory.LABELS.get(
                report_category, "Unknown"
            )
        response.source_report_reason = report_reason
        response.vote_count = vote_count
        response.keep_votes = keep_votes
        response.remove_votes = remove_votes
        items.append(response)

    return ReviewListResponse(
        total=total,
        page=page,
        per_page=per_page,
        items=items,
    )


@router.get("/reviews/{review_id}", response_model=ReviewDetailResponse)
async def get_review(
    review_id: Annotated[int, Path(description="Review ID")],
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_VIEW))],
    db: AsyncSession = Depends(get_db),
) -> ReviewDetailResponse:
    """
    Get review details including all votes.

    Requires REVIEW_VIEW permission.
    """
    result = await db.execute(
        select(  # type: ignore[call-overload]
            ImageReviews,
            Users.username,
            ImageReports.category,
            ImageReports.reason_text,
        )
        .outerjoin(Users, ImageReviews.initiated_by == Users.user_id)
        .outerjoin(ImageReports, ImageReviews.source_report_id == ImageReports.report_id)
        .where(ImageReviews.review_id == review_id)
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Review not found")

    review, initiated_by_username, report_category, report_reason = row

    # Get votes with usernames
    votes_result = await db.execute(
        select(ReviewVotes, Users.username)  # type: ignore[call-overload]
        .outerjoin(Users, ReviewVotes.user_id == Users.user_id)
        .where(ReviewVotes.review_id == review_id)
        .order_by(ReviewVotes.created_at)
    )
    votes_rows = votes_result.all()

    votes = []
    keep_votes = 0
    remove_votes = 0
    for vote, username in votes_rows:
        vote_response = VoteResponse.model_validate(vote)
        vote_response.username = username
        votes.append(vote_response)
        if vote.vote == 1:
            keep_votes += 1
        else:
            remove_votes += 1

    response = ReviewDetailResponse.model_validate(review)
    response.initiated_by_username = initiated_by_username
    response.source_report_category = report_category
    if report_category is not None:
        response.source_report_category_label = ReportCategory.LABELS.get(
            report_category, "Unknown"
        )
    response.source_report_reason = report_reason
    response.votes = votes
    response.vote_count = len(votes)
    response.keep_votes = keep_votes
    response.remove_votes = remove_votes

    return response


@router.post(
    "/images/{image_id}/review", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED
)
async def create_review(
    image_id: Annotated[int, Path(description="Image ID")],
    review_data: ReviewCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_START))],
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """
    Start a review directly on an image (without a report).

    The image status is set to REVIEW (hidden from users).

    Requires REVIEW_START permission.
    """
    # Verify image exists
    image_result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if there's already an open review for this image
    existing_review = await db.execute(
        select(ImageReviews).where(
            ImageReviews.image_id == image_id,  # type: ignore[arg-type]
            ImageReviews.status == ReviewStatus.OPEN,  # type: ignore[arg-type]
        )
    )
    if existing_review.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Image already has an open review")

    # Calculate deadline
    deadline_days = review_data.deadline_days or settings.REVIEW_DEADLINE_DAYS
    deadline = datetime.now(UTC) + timedelta(days=deadline_days)

    # Set image to review status
    previous_status = image.status
    image.status = ImageStatus.REVIEW
    image.status_user_id = current_user.user_id
    image.status_updated = datetime.now(UTC)

    # Create review
    review = ImageReviews(
        image_id=image_id,
        initiated_by=current_user.user_id,
        review_type=ReviewType.APPROPRIATENESS,
        deadline=deadline,
        status=ReviewStatus.OPEN,
        outcome=ReviewOutcome.PENDING,
    )
    db.add(review)
    await db.flush()

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REVIEW_START,
        review_id=review.review_id,
        image_id=image_id,
        details={"previous_status": previous_status, "deadline_days": deadline_days},
    )
    db.add(action)

    await db.commit()
    await db.refresh(review)

    return ReviewResponse.model_validate(review)


@router.post("/reviews/{review_id}/vote", response_model=VoteResponse)
async def vote_on_review(
    review_id: Annotated[int, Path(description="Review ID")],
    vote_data: ReviewVoteRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_VOTE))],
    db: AsyncSession = Depends(get_db),
) -> VoteResponse:
    """
    Cast or update a vote on a review.

    Admins can change their vote before the review closes.

    Requires REVIEW_VOTE permission.
    """
    # Verify review exists and is open
    review_result = await db.execute(
        select(ImageReviews).where(ImageReviews.review_id == review_id)  # type: ignore[arg-type]
    )
    review = review_result.scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.status != ReviewStatus.OPEN:
        raise HTTPException(status_code=400, detail="Review is closed")

    # Check if user already voted
    existing_vote = await db.execute(
        select(ReviewVotes).where(
            ReviewVotes.review_id == review_id,  # type: ignore[arg-type]
            ReviewVotes.user_id == current_user.user_id,  # type: ignore[arg-type]
        )
    )
    vote = existing_vote.scalar_one_or_none()

    if vote:
        # Update existing vote
        vote.vote = vote_data.vote
        vote.comment = vote_data.comment
        vote.created_at = datetime.now(UTC)
    else:
        # Create new vote
        vote = ReviewVotes(
            review_id=review_id,
            image_id=review.image_id,
            user_id=current_user.user_id,
            vote=vote_data.vote,
            comment=vote_data.comment,
        )
        db.add(vote)

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REVIEW_VOTE,
        review_id=review_id,
        image_id=review.image_id,
        details={"vote": vote_data.vote, "comment": vote_data.comment},
    )
    db.add(action)

    await db.commit()
    await db.refresh(vote)

    response = VoteResponse.model_validate(vote)
    response.username = current_user.username
    return response


@router.post("/reviews/{review_id}/close", response_model=ReviewResponse)
async def close_review(
    review_id: Annotated[int, Path(description="Review ID")],
    close_data: ReviewCloseRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_CLOSE_EARLY))],
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """
    Close a review early with a specified outcome.

    This bypasses the normal voting process and immediately applies the outcome.

    Requires REVIEW_CLOSE_EARLY permission.
    """
    result = await db.execute(
        select(ImageReviews).where(ImageReviews.review_id == review_id)  # type: ignore[arg-type]
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.status != ReviewStatus.OPEN:
        raise HTTPException(status_code=400, detail="Review is already closed")

    # Get the image
    image_result = await db.execute(
        select(Images).where(Images.image_id == review.image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Get vote counts for logging
    vote_result = await db.execute(
        select(
            func.count().label("total"),
            func.sum(ReviewVotes.vote).label("keep_votes"),
        ).where(ReviewVotes.review_id == review_id)  # type: ignore[arg-type]
    )
    vote_row = vote_result.one()
    vote_count = vote_row.total or 0
    keep_votes = int(vote_row.keep_votes or 0)
    remove_votes = vote_count - keep_votes

    # Close the review
    review.status = ReviewStatus.CLOSED
    review.outcome = close_data.outcome
    review.closed_at = datetime.now(UTC)

    # Apply outcome to image
    if close_data.outcome == ReviewOutcome.KEEP:
        image.status = ImageStatus.ACTIVE
    else:
        image.status = ImageStatus.INAPPROPRIATE
    image.status_user_id = current_user.user_id
    image.status_updated = datetime.now(UTC)

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REVIEW_CLOSE,
        review_id=review_id,
        image_id=review.image_id,
        details={
            "outcome": close_data.outcome,
            "vote_count": vote_count,
            "keep_votes": keep_votes,
            "remove_votes": remove_votes,
            "early_close": True,
        },
    )
    db.add(action)

    await db.commit()
    await db.refresh(review)

    response = ReviewResponse.model_validate(review)
    response.vote_count = vote_count
    response.keep_votes = keep_votes
    response.remove_votes = remove_votes
    return response


@router.post("/reviews/{review_id}/extend", response_model=ReviewResponse)
async def extend_review(
    review_id: Annotated[int, Path(description="Review ID")],
    extend_data: ReviewExtendRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REVIEW_START))],
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """
    Extend a review deadline.

    Only one extension is allowed per review.

    Requires REVIEW_START permission.
    """
    result = await db.execute(
        select(ImageReviews).where(ImageReviews.review_id == review_id)  # type: ignore[arg-type]
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.status != ReviewStatus.OPEN:
        raise HTTPException(status_code=400, detail="Review is closed")

    if review.extension_used:
        raise HTTPException(status_code=400, detail="Extension has already been used")

    # Calculate new deadline
    extension_days = extend_data.days or settings.REVIEW_EXTENSION_DAYS
    old_deadline = review.deadline
    review.deadline = datetime.now(UTC) + timedelta(days=extension_days)
    review.extension_used = 1

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REVIEW_EXTEND,
        review_id=review_id,
        image_id=review.image_id,
        details={
            "old_deadline": old_deadline.isoformat() if old_deadline else None,
            "new_deadline": review.deadline.isoformat(),
            "extension_days": extension_days,
        },
    )
    db.add(action)

    await db.commit()
    await db.refresh(review)

    return ReviewResponse.model_validate(review)


# ===== User Suspensions =====


@router.post("/users/{user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    user_id: Annotated[int, Path(description="User ID to suspend or warn")],
    suspend_data: SuspendUserRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.USER_BAN))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Suspend or warn a user account.

    For suspensions (action="suspended"):
    - Set user.active = 0
    - Set suspension details (reason, expiry)
    - Revoke all refresh tokens (logout from all devices)
    - Log to user_suspensions audit table

    For warnings (action="warning"):
    - Log warning to user_suspensions audit table only
    - User account remains active

    Requires USER_BAN permission.
    """
    # Verify user exists
    result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_warning = suspend_data.action == SuspensionAction.WARNING

    # Prevent self-suspension (but allow self-warning for testing? No, block both)
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot suspend or warn yourself.",
        )

    # For suspensions, check if user is already suspended
    if not is_warning:
        suspension_result = await db.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user_id)  # type: ignore[arg-type]
            .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
        )
        suspension_records = suspension_result.scalars().all()

        # Find the latest "suspended" record
        latest_suspended = next(
            (r for r in suspension_records if r.action == SuspensionAction.SUSPENDED), None
        )
        if latest_suspended:
            # Check if there is a "reactivated" record after it
            reactivated_after = any(
                r.action == SuspensionAction.REACTIVATED
                and r.actioned_at > latest_suspended.actioned_at
                for r in suspension_records
            )
            # Only block if still suspended (no reactivation after, or suspension still active)
            if not reactivated_after and (
                latest_suspended.suspended_until is None
                or latest_suspended.suspended_until > datetime.now(UTC).replace(tzinfo=None)
            ):
                raise HTTPException(status_code=400, detail="User is already suspended")

        # Suspend the user
        user.active = 0

        # Revoke all refresh tokens (logout from all devices)
        await db.execute(delete(RefreshTokens).where(RefreshTokens.user_id == user_id))  # type: ignore[arg-type]

    # Log to audit trail
    suspension_record = UserSuspensions(
        user_id=user_id,
        action=suspend_data.action,
        actioned_by=current_user.user_id,
        suspended_until=None if is_warning else suspend_data.suspended_until,
        reason=suspend_data.reason,
    )
    db.add(suspension_record)

    await db.commit()

    if is_warning:
        return MessageResponse(message="Warning issued to user")

    suspension_type = (
        "indefinitely"
        if suspend_data.suspended_until is None
        else f"until {suspend_data.suspended_until}"
    )
    return MessageResponse(message=f"User suspended {suspension_type}")


@router.post("/users/{user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    user_id: Annotated[int, Path(description="User ID to reactivate")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.USER_BAN))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Reactivate a suspended user account.

    This clears suspension status and allows the user to login again.

    Requires USER_BAN permission.
    """
    # Verify user exists
    result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is actually suspended (has an active suspension record)
    # Find all suspension records for this user
    suspension_result = await db.execute(
        select(UserSuspensions)
        .where(UserSuspensions.user_id == user_id)  # type: ignore[arg-type]
        .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
    )
    suspension_records = suspension_result.scalars().all()

    # Find the latest "suspended" record
    latest_suspended = next(
        (r for r in suspension_records if r.action == SuspensionAction.SUSPENDED), None
    )
    if not latest_suspended:
        raise HTTPException(status_code=400, detail="User has no suspension record")

    # Check if there is a "reactivated" record after the latest "suspended"
    reactivated_after = any(
        r.action == SuspensionAction.REACTIVATED and r.actioned_at > latest_suspended.actioned_at
        for r in suspension_records
    )
    if reactivated_after:
        raise HTTPException(status_code=400, detail="User is not currently suspended")

    # Reactivate the user
    user.active = 1

    # Log to audit trail
    reactivation_record = UserSuspensions(
        user_id=user_id,
        action=SuspensionAction.REACTIVATED,
        actioned_by=current_user.user_id,
    )
    db.add(reactivation_record)

    await db.commit()

    return MessageResponse(message="User reactivated successfully")


@router.get("/users/{user_id}/suspensions", response_model=SuspensionListResponse)
async def get_user_suspensions(
    user_id: Annotated[int, Path(description="User ID")],
    _: Annotated[None, Depends(require_permission(Permission.USER_BAN))],
    db: AsyncSession = Depends(get_db),
) -> SuspensionListResponse:
    """
    Get suspension history for a user.

    Returns all suspension and reactivation records.

    Requires USER_BAN permission.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get suspension history
    result = await db.execute(
        select(UserSuspensions)
        .where(UserSuspensions.user_id == user_id)  # type: ignore[arg-type]
        .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
    )
    suspensions = result.scalars().all()

    return SuspensionListResponse(
        user_id=user_id,
        username=user.username,
        total=len(suspensions),
        suspensions=[SuspensionResponse.model_validate(s) for s in suspensions],
    )

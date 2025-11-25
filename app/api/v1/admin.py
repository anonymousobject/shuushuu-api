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

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    AdminActionType,
    ImageStatus,
    ReportStatus,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
    SuspensionAction,
    settings,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permission_deps import require_all_permissions, require_permission
from app.core.permissions import Permission
from app.models.admin_action import AdminActions
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.image_review import ImageReviews
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups, UserPerms
from app.models.refresh_token import RefreshTokens
from app.models.review_vote import ReviewVotes
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
    ReportActionRequest,
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


# ===== Report Triage =====


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    _: Annotated[None, Depends(require_permission(Permission.REPORT_VIEW))],
    status_filter: Annotated[
        int | None,
        Query(alias="status", description="Filter by status (0=pending, 1=reviewed, 2=dismissed)"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    db: AsyncSession = Depends(get_db),
) -> ReportListResponse:
    """
    List image reports in the triage queue.

    Requires REPORT_VIEW permission.
    """
    query = select(ImageReports)

    if status_filter is not None:
        query = query.where(ImageReports.status == status_filter)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering (newest first)
    offset = (page - 1) * per_page
    query = query.order_by(desc(ImageReports.created_at)).offset(offset).limit(per_page)  # type: ignore[arg-type]

    result = await db.execute(query)
    reports = result.scalars().all()

    return ReportListResponse(
        total=total,
        page=page,
        per_page=per_page,
        items=[ReportResponse.model_validate(r) for r in reports],
    )


@router.post("/reports/{report_id}/dismiss", response_model=MessageResponse)
async def dismiss_report(
    report_id: Annotated[int, Path(description="Report ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_MANAGE))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Dismiss a report without taking action on the image.

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

    # Update report
    report.status = ReportStatus.DISMISSED
    report.reviewed_by = current_user.user_id
    report.reviewed_at = datetime.now(UTC)

    # Log action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.REPORT_DISMISS,
        report_id=report_id,
        image_id=report.image_id,
        details={},
    )
    db.add(action)

    await db.commit()

    return MessageResponse(message="Report dismissed successfully")


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
    query = select(ImageReviews)

    if status_filter is not None:
        query = query.where(ImageReviews.status == status_filter)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering (newest first)
    offset = (page - 1) * per_page
    query = query.order_by(desc(ImageReviews.created_at)).offset(offset).limit(per_page)  # type: ignore[arg-type]

    result = await db.execute(query)
    reviews = result.scalars().all()

    # Build responses with vote counts
    items = []
    for review in reviews:
        # Get vote counts
        vote_result = await db.execute(
            select(
                func.count().label("total"),
                func.sum(ReviewVotes.vote).label("keep_votes"),  # vote=1 is keep
            ).where(ReviewVotes.review_id == review.review_id)  # type: ignore[arg-type]
        )
        vote_row = vote_result.one()
        vote_count = vote_row.total or 0
        keep_votes = int(vote_row.keep_votes or 0)
        remove_votes = vote_count - keep_votes

        response = ReviewResponse.model_validate(review)
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
        select(ImageReviews).where(ImageReviews.review_id == review_id)  # type: ignore[arg-type]
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

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
    user_id: Annotated[int, Path(description="User ID to suspend")],
    suspend_data: SuspendUserRequest,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.USER_BAN))],
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Suspend a user account.

    This will:
    - Set user.active = 0
    - Set suspension details (reason, expiry)
    - Revoke all refresh tokens (logout from all devices)
    - Log suspension to user_suspensions audit table

    Requires USER_BAN permission.
    """
    # Verify user exists
    result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-suspension
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot suspend yourself.",
        )

    # Check if user is already suspended (check user_suspensions table)
    suspension_result = await db.execute(
        select(UserSuspensions)
        .where(UserSuspensions.user_id == user_id)
        .order_by(desc(UserSuspensions.actioned_at))
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
        action=SuspensionAction.SUSPENDED,
        actioned_by=current_user.user_id,
        suspended_until=suspend_data.suspended_until,
        reason=suspend_data.reason,
    )
    db.add(suspension_record)

    await db.commit()

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

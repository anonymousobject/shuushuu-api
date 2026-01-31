"""
Comments API endpoints
"""

from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import CommentSortParams, PaginationParams
from app.config import AdminActionType, ReportStatus
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models import Comments, Images, Users
from app.models.admin_action import AdminActions
from app.models.comment_report import CommentReports
from app.models.permissions import UserGroups
from app.schemas.comment import (
    CommentCreate,
    CommentListResponse,
    CommentResponse,
    CommentStatsResponse,
    CommentUpdate,
)
from app.schemas.comment_report import CommentReportCreate, CommentReportResponse

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("/", response_model=CommentListResponse, include_in_schema=False)
@router.get("", response_model=CommentListResponse)
async def list_comments(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[CommentSortParams, Depends()],
    # Filters
    image_id: Annotated[int | None, Query(description="Filter by image ID")] = None,
    image_ids: Annotated[
        str | None, Query(description="Filter by multiple image IDs (comma-separated)")
    ] = None,
    user_id: Annotated[int | None, Query(description="Filter by user ID")] = None,
    search_text: Annotated[str | None, Query(description="Search in comment text")] = None,
    search_mode: Annotated[
        str | None,
        Query(
            pattern="^(natural|boolean|like)$",
            description="Search mode: natural (default), boolean fulltext, or LIKE",
        ),
    ] = None,
    # Date filtering
    date_from: Annotated[str | None, Query(description="Start date (YYYY-MM-DD)")] = None,
    date_to: Annotated[str | None, Query(description="End date (YYYY-MM-DD)")] = None,
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    # TODO: Add an automatic mode that detects the most efficient method based on input?
    """
    Search and list comments with filtering and flexible text search.

    **Supports:**
    - Pagination (page, per_page)
    - Sorting by date, post_id, or update_count
    - Filter by image, user, or text search
    - Date range filtering
    - Multiple search modes (LIKE, natural fulltext, boolean fulltext)

    **Search Modes:**
    - `natural` (default): MySQL fulltext natural language search (10-100x faster, relevance ranking)
    - `boolean`: MySQL fulltext boolean search with operators
    - `like`: Simple pattern matching, works anywhere. Example: `?search_text=awesome`

    **Boolean Mode Examples:**
    - `+awesome -terrible`: Must contain "awesome", must not contain "terrible"
    - `"exact phrase"`: Search for exact phrase
    - `word*`: Wildcard search

    **Examples:**
    - `/comments?image_id=123` - All comments on image 123
    - `/comments?image_ids=123,456,789` - All comments on multiple images (efficient for N images)
    - `/comments?user_id=5` - All comments by user 5
    - `/comments?search_text=awesome` - Fast fulltext search
    - `/comments?search_text=awesome&search_mode=like` - Simple search using LIKE
    - `/comments?search_text=awesome&search_mode=natural` - Fast fulltext search, same as default
    - `/comments?search_text=+great -bad&search_mode=boolean` - Boolean fulltext
    - `/comments?date_from=2024-01-01` - Comments from 2024 onwards
    """
    from sqlalchemy import text as sql_text

    # Build base query - exclude deleted comments
    query = select(Comments).where(Comments.deleted == False)  # type: ignore[arg-type]  # noqa: E712

    # Apply filters
    if image_id is not None:
        query = query.where(Comments.image_id == image_id)  # type: ignore[arg-type]
    elif image_ids is not None:
        # Parse comma-separated image IDs
        try:
            image_id_list = [int(x.strip()) for x in image_ids.split(",") if x.strip()]
            if image_id_list:
                query = query.where(Comments.image_id.in_(image_id_list))  # type: ignore[union-attr]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid image_ids format") from None
    if user_id is not None:
        query = query.where(Comments.user_id == user_id)  # type: ignore[arg-type]

    # Text search with mode selection
    if search_text:
        # Default to natural if no mode specified
        effective_mode = search_mode or "natural"

        if effective_mode == "boolean":
            # Boolean fulltext: supports +word, -word, "phrase", word*
            match_expr = sql_text("MATCH(post_text) AGAINST(:query IN BOOLEAN MODE)")
            query = query.where(match_expr).params(query=search_text)
        elif effective_mode == "natural":
            # Natural language fulltext: ranks by relevance
            match_expr = sql_text("MATCH(post_text) AGAINST(:query IN NATURAL LANGUAGE MODE)")
            query = query.where(match_expr).params(query=search_text)
        else:  # like
            # Simple pattern matching (slowest but works everywhere)
            query = query.where(Comments.post_text.like(f"%{search_text}%"))  # type: ignore

    # Date filtering
    if date_from:
        query = query.where(Comments.date >= date_from)  # type: ignore[operator]
    if date_to:
        query = query.where(Comments.date <= date_to)  # type: ignore[operator]

    # Count total results
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Comments, sorting.sort_by)

    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Eager load user and groups to avoid N+1 queries
    query = query.options(
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )

    # Execute query
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments],
    )


@router.get("/{comment_id}", response_model=CommentResponse)
async def get_comment(comment_id: int, db: AsyncSession = Depends(get_db)) -> CommentResponse:
    """
    Get a single comment by ID.

    Returns detailed information about a comment including metadata
    and update history.
    """
    result = await db.execute(
        select(Comments)
        .options(
            selectinload(Comments.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Comments.post_id == comment_id)  # type: ignore[arg-type]
        .where(Comments.deleted == False)  # type: ignore[arg-type]  # noqa: E712
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    return CommentResponse.model_validate(comment)


@router.get("/image/{image_id}", response_model=CommentListResponse)
async def get_image_comments(
    image_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[CommentSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    """
    Get all comments for a specific image.

    This is a convenience endpoint that wraps the main list endpoint
    with automatic image_id filtering. Useful for displaying comment
    threads on image detail pages.
    """
    # Verify image exists
    image_result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Query comments for this image - exclude deleted comments
    query = select(Comments).where(
        Comments.image_id == image_id,  # type: ignore[arg-type]
        Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Comments, sorting.sort_by)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Eager load user and groups to avoid N+1 queries
    query = query.options(
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )

    # Execute
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments],
    )


@router.get("/user/{user_id}", response_model=CommentListResponse)
async def get_user_comments(
    user_id: int,
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[CommentSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    """
    Get all comments by a specific user.

    This is a convenience endpoint for user profile pages showing
    their comment history across all images.
    """
    # Verify user exists
    user_result = await db.execute(
        select(Users).where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Query comments by this user - exclude deleted comments
    query = select(Comments).where(
        Comments.user_id == user_id,  # type: ignore[arg-type]
        Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Comments, sorting.sort_by)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Eager load user and groups to avoid N+1 queries
    query = query.options(
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )

    # Execute
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments],
    )


@router.get("/stats/summary", response_model=CommentStatsResponse)
async def get_comment_stats(db: AsyncSession = Depends(get_db)) -> CommentStatsResponse:
    """
    Get overall comment statistics.

    Returns:
    - Total number of comments
    - Number of images with at least one comment
    - Average comments per image (across all images with comments)
    """
    # Total comments
    total_result = await db.execute(select(func.count(Comments.post_id)))  # type: ignore[arg-type]
    total_comments = total_result.scalar() or 0

    # Count distinct images with comments
    distinct_images_result = await db.execute(select(func.count(func.distinct(Comments.image_id))))
    total_images_with_comments = distinct_images_result.scalar() or 0

    # Calculate average
    if total_images_with_comments > 0:
        average_comments = total_comments / total_images_with_comments
    else:
        average_comments = 0.0

    return CommentStatsResponse(
        total_comments=total_comments,
        total_images_with_comments=total_images_with_comments,
        average_comments_per_image=round(average_comments, 2),
    )


@router.post(
    "/",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post("", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def create_comment(
    body: CommentCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CommentResponse:
    """
    Create a new comment or reply.

    **Request Body:**
    - `image_id` (required): Image to comment on
    - `post_text` (required): Comment text (supports markdown)
    - `parent_comment_id` (optional): Post ID of parent comment for replies

    **Returns:** 201 Created with full comment details

    **Errors:**
    - 400: Empty comment text or invalid parent_comment_id
    - 401: Not authenticated
    - 403: Comments are locked on the image
    - 404: Image or parent comment not found
    """
    # Validate image exists
    image_result = await db.execute(
        select(Images).where(Images.image_id == body.image_id)  # type: ignore[arg-type]
    )
    image = image_result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check if image is locked
    if image.locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Comments are locked on this image",
        )

    # Validate parent_comment_id if provided
    if body.parent_comment_id is not None:
        parent_result = await db.execute(
            select(Comments).where(
                Comments.post_id == body.parent_comment_id,  # type: ignore[arg-type]
                Comments.deleted == False,  # type: ignore[arg-type]  # noqa: E712
            )
        )
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent comment not found")

        # Ensure parent comment is on the same image
        if parent.image_id != body.image_id:
            raise HTTPException(
                status_code=400,
                detail="Parent comment must be on the same image",
            )

    # Create comment
    comment = Comments(
        image_id=body.image_id,
        post_text=body.post_text,
        parent_comment_id=body.parent_comment_id,
        user_id=current_user.user_id,  # Use authenticated user's ID
        date=datetime.now(UTC),
        update_count=0,
    )

    db.add(comment)
    await db.commit()

    # Re-fetch with eager loading for groups
    result = await db.execute(
        select(Comments)
        .options(
            selectinload(Comments.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Comments.post_id == comment.post_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one()

    return CommentResponse.model_validate(comment)


@router.patch("/{comment_id}", response_model=CommentResponse)
async def update_comment(
    comment_id: int,
    body: CommentUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CommentResponse:
    """
    Edit an existing comment.

    **Request Body:**
    - `post_text` (required): Updated comment text

    **Returns:** 200 OK with updated comment details

    **Errors:**
    - 401: Not authenticated
    - 403: User doesn't own the comment
    - 404: Comment not found
    """
    # Load comment
    result = await db.execute(
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify ownership
    if comment.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own comments",
        )

    # Update comment
    comment.post_text = body.post_text
    comment.update_count += 1
    comment.last_updated = datetime.now(UTC)
    comment.last_updated_user_id = current_user.user_id

    await db.commit()

    # Re-fetch with eager loading for groups
    result = await db.execute(
        select(Comments)
        .options(
            selectinload(Comments.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one()

    return CommentResponse.model_validate(comment)


@router.delete("/{comment_id}", response_model=CommentResponse)
async def delete_comment(
    comment_id: int,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> CommentResponse:
    """
    Delete a comment (soft-delete).

    - Comment owners can delete their own comments
    - Users with POST_EDIT permission can delete any comment

    **Returns:** 200 OK with updated comment (deleted flag set to True)

    **Errors:**
    - 401: Not authenticated
    - 403: User doesn't own the comment and lacks POST_EDIT permission
    - 404: Comment not found
    """
    # Load comment
    result = await db.execute(
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    # Check authorization: owner or has POST_EDIT permission
    is_owner = comment.user_id == current_user.user_id
    has_mod_permission = await has_permission(
        db, current_user.user_id, Permission.POST_EDIT, redis_client
    )

    if not is_owner and not has_mod_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments",
        )

    # Log admin action if moderator deleting someone else's comment
    if not is_owner and has_mod_permission:
        action = AdminActions(
            user_id=current_user.user_id,
            action_type=AdminActionType.COMMENT_DELETE,
            image_id=comment.image_id,
            details={
                "comment_id": comment_id,
                "original_user_id": comment.user_id,
                "post_text_preview": comment.post_text[:100] if comment.post_text else None,
            },
        )
        db.add(action)

    # Soft delete: Set deleted flag to True
    # This preserves conversation flow and reply threading
    # Database trigger will automatically decrement post counts
    comment.deleted = True
    comment.post_text = "[deleted]"  # Also update text for backwards compatibility

    # Detach child comments (SET NULL for replies)
    # Since we're soft-deleting instead of hard-deleting, the database's
    # ON DELETE SET NULL constraint won't fire, so we manually detach children
    await db.execute(
        update(Comments)
        .where(Comments.parent_comment_id == comment_id)  # type: ignore[arg-type]
        .values(parent_comment_id=None)
    )

    await db.commit()

    # Re-fetch with eager loading for groups
    result = await db.execute(
        select(Comments)
        .options(
            selectinload(Comments.user)  # type: ignore[arg-type]
            .selectinload(Users.user_groups)  # type: ignore[arg-type]
            .selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one()

    return CommentResponse.model_validate(comment)


@router.post("/{comment_id}/report", response_model=CommentReportResponse, status_code=201)
async def report_comment(
    comment_id: int,
    report_data: CommentReportCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CommentReportResponse:
    """
    Report a comment for rule violations.

    Categories:
    - 1: Rule Violation (harassment, illegal content, etc.)
    - 2: Spam
    - 127: Other

    Rate limit: One pending report per user per comment.
    """
    # Check comment exists and is not deleted
    result = await db.execute(
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.deleted:
        raise HTTPException(status_code=400, detail="Cannot report a deleted comment")

    # Check for existing pending report from this user
    result = await db.execute(
        select(CommentReports).where(
            CommentReports.comment_id == comment_id,  # type: ignore[arg-type]
            CommentReports.user_id == current_user.user_id,  # type: ignore[arg-type]
            CommentReports.status == ReportStatus.PENDING,  # type: ignore[arg-type]
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="You already have a pending report on this comment",
        )

    # Create the report
    report = CommentReports(
        comment_id=comment_id,
        user_id=current_user.user_id,
        category=report_data.category,
        reason_text=report_data.reason_text,
        status=ReportStatus.PENDING,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return CommentReportResponse(
        report_id=report.report_id or 0,
        comment_id=report.comment_id,
        image_id=comment.image_id or 0,
        user_id=report.user_id,
        category=report.category,
        reason_text=report.reason_text,
        status=report.status,
        created_at=report.created_at,  # type: ignore[arg-type]
    )

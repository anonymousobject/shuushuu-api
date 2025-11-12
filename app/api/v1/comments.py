"""
Comments API endpoints
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CommentSortParams, PaginationParams
from app.core.database import get_db
from app.models import Comments, Images, Users
from app.schemas.comment import (
    CommentListResponse,
    CommentResponse,
    CommentStatsResponse,
)

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("/", response_model=CommentListResponse)
async def list_comments(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[CommentSortParams, Depends()],
    # Filters
    image_id: Annotated[int | None, Query(description="Filter by image ID")] = None,
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
    - `/comments?user_id=5` - All comments by user 5
    - `/comments?search_text=awesome` - Fast fulltext search
    - `/comments?search_text=awesome&search_mode=like` - Simple search using LIKE
    - `/comments?search_text=awesome&search_mode=natural` - Fast fulltext search, same as default
    - `/comments?search_text=+great -bad&search_mode=boolean` - Boolean fulltext
    - `/comments?date_from=2024-01-01` - Comments from 2024 onwards
    """
    from sqlalchemy import text as sql_text

    # Build base query
    query = select(Comments)

    # Apply filters
    if image_id is not None:
        query = query.where(Comments.image_id == image_id)  # type: ignore[arg-type]
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
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
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

    # Query comments for this image
    query = select(Comments).where(Comments.image_id == image_id)  # type: ignore[arg-type]

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

    # Query comments by this user
    query = select(Comments).where(Comments.user_id == user_id)  # type: ignore[arg-type]

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

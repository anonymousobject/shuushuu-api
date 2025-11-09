"""
Comments API endpoints
"""
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Comments, Images, Users
from app.schemas.comment import (
    CommentListResponse,
    CommentResponse,
    CommentStatsResponse,
)

router = APIRouter(prefix="/comments", tags=["comments"])


class SortOrder(str, Enum):
    """Sort order options."""
    ASC = "ASC"
    DESC = "DESC"


class CommentSortBy(str, Enum):
    """Allowed sort fields for comment queries."""
    post_id = "post_id"
    date = "date"
    update_count = "update_count"


@router.get("/{comment_id}", response_model=CommentResponse)
async def get_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db)
) -> CommentResponse:
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


@router.get("/", response_model=CommentListResponse)
async def list_comments(
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),

    # Sorting
    sort_by: CommentSortBy = Query(CommentSortBy.date, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),

    # Filters
    image_id: int | None = Query(None, description="Filter by image ID"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    search_text: str | None = Query(None, description="Search in comment text (partial match)"),

    # Date filtering
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),

    db: AsyncSession = Depends(get_db)
) -> CommentListResponse:
    """
    Search and list comments with filtering.

    **Supports:**
    - Pagination (page, per_page)
    - Sorting by date, post_id, or update_count
    - Filter by image, user, or text search
    - Date range filtering

    **Examples:**
    - `/comments?image_id=123` - All comments on image 123
    - `/comments?user_id=5` - All comments by user 5
    - `/comments?search_text=awesome&sort_by=date` - Search for "awesome" in comments
    - `/comments?date_from=2024-01-01` - Comments from 2024 onwards
    """
    # Build base query
    query = select(Comments)

    # Apply filters
    if image_id is not None:
        query = query.where(Comments.image_id == image_id)  # type: ignore[arg-type]
    if user_id is not None:
        query = query.where(Comments.user_id == user_id)  # type: ignore[arg-type]
    if search_text:
        query = query.where(getattr(Comments, "post_text").like(f"%{search_text}%"))

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
    offset = (page - 1) * per_page
    sort_column = getattr(Comments, sort_by.value)

    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(offset).limit(per_page)

    # Execute query
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments]
    )


@router.get("/image/{image_id}", response_model=CommentListResponse)
async def get_image_comments(
    image_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: CommentSortBy = Query(CommentSortBy.date, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
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
    sort_column = getattr(Comments, sort_by.value)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments]
    )


@router.get("/user/{user_id}", response_model=CommentListResponse)
async def get_user_comments(
    user_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: CommentSortBy = Query(CommentSortBy.date, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
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
    sort_column = getattr(Comments, sort_by.value)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments]
    )


@router.get("/search/text", response_model=CommentListResponse)
async def search_comments(
    query_text: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    mode: str = Query("natural", pattern="^(natural|boolean)$", description="Search mode: natural or boolean"),
    sort_by: CommentSortBy = Query(CommentSortBy.date, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
) -> CommentListResponse:
    """
    Search for comments using MySQL full-text search.

    This endpoint uses MySQL's FULLTEXT index for faster searching on large datasets.
    Requires a FULLTEXT index on the post_text column.

    **Search Modes:**
    - `natural` (default): Natural language search, ranks results by relevance
    - `boolean`: Boolean search supporting operators (+word, -word, "phrase", etc.)

    **Boolean Mode Examples:**
    - `+awesome -terrible`: Must contain "awesome", must not contain "terrible"
    - `"exact phrase"`: Search for exact phrase
    - `word*`: Wildcard search

    **Performance:**
    Full-text search is 10-100x faster than LIKE pattern matching on large datasets.
    """
    from sqlalchemy import text as sql_text

    # Build full-text search query based on mode
    if mode == "boolean":
        # Boolean mode: supports +word, -word, "phrase", word*, etc.
        match_expr = sql_text(
            "MATCH(post_text) AGAINST(:query IN BOOLEAN MODE)"
        )
    else:
        # Natural language mode: ranks by relevance
        match_expr = sql_text(
            "MATCH(post_text) AGAINST(:query IN NATURAL LANGUAGE MODE)"
        )

    # Build query with full-text search
    query = select(Comments).where(match_expr).params(query=query_text)

    # Count total results
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Comments, sort_by.value)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    comments = result.scalars().all()

    return CommentListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        comments=[CommentResponse.model_validate(comment) for comment in comments]
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
    distinct_images_result = await db.execute(
        select(func.count(func.distinct(Comments.image_id)))
    )
    total_images_with_comments = distinct_images_result.scalar() or 0

    # Calculate average
    if total_images_with_comments > 0:
        average_comments = total_comments / total_images_with_comments
    else:
        average_comments = 0.0

    return CommentStatsResponse(
        total_comments=total_comments,
        total_images_with_comments=total_images_with_comments,
        average_comments_per_image=round(average_comments, 2)
    )

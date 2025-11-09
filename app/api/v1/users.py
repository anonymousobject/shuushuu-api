"""
Users API endpoints
"""
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Favorites, Images, Users
from app.models.image import ImageSortBy  # Import shared enum
from app.schemas.image import ImageListResponse, ImageResponse
from app.schemas.user import UserListResponse, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


class SortOrder(str, Enum):
    """Sort order options."""
    ASC = "ASC"
    DESC = "DESC"


@router.get("/{user_id}/images", response_model=ImageListResponse)
async def get_user_images(
    user_id: int = Path(..., description="User ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: ImageSortBy = Query(ImageSortBy.image_id, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
) -> ImageListResponse:
    """
    Get all images uploaded by a specific user.

    This is a convenience endpoint for the common case of viewing a user's uploads.
    For more complex filtering, use `/images?user_id={id}&...` instead.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's images
    query = select(Images).where(Images.user_id == user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Images, sort_by.value)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        images=[ImageResponse.model_validate(img) for img in images]
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int = Path(..., description="User ID"),
    db: AsyncSession = Depends(get_db)
) -> UserResponse:
    """
    Get user profile information.
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)

@router.get("/", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db)
) -> UserListResponse:
    """
    List users with pagination.
    """
    query = select(Users)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Sort by user ID
    query = query.order_by(asc(Users.user_id))  # type: ignore[arg-type]

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        users=[UserResponse.model_validate(user) for user in users]
    )


@router.get("/{user_id}/favorites", response_model=ImageListResponse)
async def get_user_favorites(
    user_id: int = Path(..., description="User ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: ImageSortBy = Query(ImageSortBy.image_id, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort order"),
    db: AsyncSession = Depends(get_db)
) -> ImageListResponse:
    """
    Get all images favorited by a specific user.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's favorite images
    query = select(Images).join(Favorites).where(Favorites.user_id == user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Images, sort_by.value)
    if sort_order == SortOrder.DESC:
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=page,
        per_page=per_page,
        images=[ImageResponse.model_validate(img) for img in images]
    )

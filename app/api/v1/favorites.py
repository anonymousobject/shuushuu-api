"""
Favorites API endpoints (DEPRECATED)

These routes are deprecated and maintained for backward compatibility.
Use the new RESTful routes instead:
- GET /users/{user_id}/favorites - Get user's favorite images
- GET /images/{image_id}/favorites - Get users who favorited an image
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Favorites, Images, Users
from app.models.image import ImageSortBy
from app.schemas.image import ImageListResponse, ImageResponse
from app.schemas.user import UserListResponse, UserResponse

router = APIRouter(prefix="/favorites", tags=["favorites (deprecated)"])


@router.get("/user/{user_id}", response_model=ImageListResponse, deprecated=True)
async def get_favorite_images(
    user_id: int = Path(..., description="User ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: ImageSortBy = Query(ImageSortBy.image_id, description="Sort field"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$", description="Sort order"),
    db: AsyncSession = Depends(get_db),
) -> ImageListResponse:
    """
    Get all images favorited by a specific user.

    **DEPRECATED**: Use GET /users/{user_id}/favorites instead.
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
    sort_column = getattr(Images, sort_by, Images.image_id)
    if sort_order == "DESC":
        query = query.order_by(desc(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(asc(sort_column))  # type: ignore[arg-type]

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
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.get("/image/{image_id}", response_model=UserListResponse, deprecated=True)
async def get_image_favorites(
    image_id: int = Path(..., description="Image ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("user_id", description="Sort field (user_id, date_joined, etc)"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$", description="Sort order"),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    """
    Get all users who have favorited a specific image.

    **DEPRECATED**: Use GET /images/{image_id}/favorites instead.
    """
    # Verify image exists
    image_result = await db.execute(select(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    image = image_result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Get users who favorited the image
    query = select(Users).join(Favorites).where(Favorites.image_id == image_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Users, sort_by, Users.user_id)
    if sort_order == "DESC":
        query = query.order_by(desc(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(asc(sort_column))  # type: ignore[arg-type]

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
        users=[UserResponse.model_validate(user) for user in users],
    )

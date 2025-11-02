"""
Users API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Images, Users
from app.schemas.image import ImageListResponse
from app.schemas.user import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}/images", response_model=ImageListResponse)
async def get_user_images(
    user_id: int = Path(..., description="User ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("image_id", description="Sort field"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$", description="Sort order"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all images uploaded by a specific user.

    This is a convenience endpoint for the common case of viewing a user's uploads.
    For more complex filtering, use `/images?user_id={id}&...` instead.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's images
    query = select(Images).where(Images.user_id == user_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = getattr(Images, sort_by, Images.image_id)
    if sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "images": images
    }


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int = Path(..., description="User ID"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user profile information.
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user

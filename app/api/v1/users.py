"""
Users API endpoints
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import ImageSortParams, PaginationParams, UserSortParams
from app.core.auth import get_current_user, get_current_user_id
from app.core.database import get_db
from app.core.security import get_password_hash, validate_password_strength
from app.models import Favorites, Images, Users
from app.schemas.image import ImageListResponse, ImageResponse
from app.schemas.user import UserCreate, UserListResponse, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=UserListResponse)
async def list_users(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[UserSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
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
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        users=[UserResponse.model_validate(user) for user in users],
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get the profile of the currently authenticated user.
    """
    user_result = await db.execute(select(Users).where(Users.user_id == current_user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)


@router.patch("/me", response_model=UserResponse)
async def update_current_user_profile(
    user_data: UserUpdate,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Update the profile of the currently authenticated user.

    All fields are optional. Only provided fields will be updated.
    """
    return await _update_user_profile(current_user_id, user_data, current_user_id, db)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user_profile(
    user_id: Annotated[int, Path(description="User ID to update")],
    user_data: UserUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Update a user's profile.

    - Regular users can only update their own profile (user_id must match their ID)
    - Admins can update any user's profile

    All fields are optional. Only provided fields will be updated.
    """
    # Check permission: user can update themselves, or must be admin
    if current_user.user_id != user_id and not current_user.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    return await _update_user_profile(user_id, user_data, current_user.user_id, db)


async def _update_user_profile(
    user_id: int,
    user_data: UserUpdate,
    current_user_id: int,
    db: AsyncSession,
) -> UserResponse:
    """
    Internal function to handle user profile updates.

    Args:
        user_id: ID of user to update
        user_data: Update data
        current_user_id: ID of user making the request (for email uniqueness check)
        db: Database session

    Returns:
        Updated user response
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update only provided fields
    update_data = user_data.model_dump(exclude_unset=True)

    # Handle password separately with validation and hashing
    if "password" in update_data:
        password = update_data.pop("password")
        is_valid, error_message = validate_password_strength(password)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)
        user.password = get_password_hash(password)
        user.password_type = "bcrypt"

    # Handle email validation
    if "email" in update_data:
        email = update_data["email"]
        # Check if email is already taken by another user
        existing_email = await db.execute(
            select(Users).where(Users.email == email, Users.user_id != user_id)  # type: ignore[arg-type]
        )
        if existing_email.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already in use")

    # Update remaining fields
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    return UserResponse.model_validate(user)


@router.get("/{user_id}/images", response_model=ImageListResponse)
async def get_user_images(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
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
    sort_column = getattr(Images, sorting.sort_by.value)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: Annotated[int, Path(description="User ID")],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get user profile information.
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)


@router.get("/{user_id}/favorites", response_model=ImageListResponse)
async def get_user_favorites(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
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
    sort_column = getattr(Images, sorting.sort_by.value)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageResponse.model_validate(img) for img in images],
    )


@router.post("/", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Create a new user.
    """

    # TODO: extract these checks into reusable functions

    # check username format
    if not re.match(r"^[a-zA-Z0-9_.-]{3,20}$", user_data.username):
        raise HTTPException(status_code=400, detail="Invalid username format")

    # check if username or email already exists
    existing_user = await db.execute(
        select(Users).where(
            or_(
                Users.username == user_data.username,  # type: ignore[arg-type]
                Users.email == user_data.email,  # type: ignore[arg-type]
            )
        )
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username or email already exists")

    # Check the password strength
    is_valid, error_message = validate_password_strength(user_data.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    new_user = Users(
        username=user_data.username,
        password=get_password_hash(user_data.password),
        password_type="bcrypt",  # Mark as bcrypt password
        salt="",  # Legacy field - empty for bcrypt users
        email=user_data.email,
        active=1,  # New users are active by default
        admin=0,  # New users are not admin
        # Other fields use model defaults
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return UserResponse.model_validate(new_user)

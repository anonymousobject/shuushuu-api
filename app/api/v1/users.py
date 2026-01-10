"""
Users API endpoints
"""

import hashlib
import re
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path as FilePath
from typing import Annotated

import redis.asyncio as redis
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import asc, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import ImageSortParams, PaginationParams, UserSortParams
from app.config import SuspensionAction
from app.core.auth import get_client_ip, get_current_user, get_current_user_id
from app.core.database import get_db
from app.core.permission_cache import get_cached_user_permissions
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.core.security import get_password_hash, validate_password_strength
from app.models import Favorites, Images, TagLinks, Users
from app.models.permissions import UserGroups
from app.models.user_suspension import UserSuspensions
from app.schemas.image import ImageDetailedListResponse, ImageDetailedResponse
from app.schemas.user import (
    AcknowledgeWarningsResponse,
    UserCreate,
    UserCreateResponse,
    UserListResponse,
    UserPrivateResponse,
    UserResponse,
    UserUpdate,
    UserWarningResponse,
    UserWarningsResponse,
)
from app.services.avatar import (
    delete_avatar_if_orphaned,
    resize_avatar,
    save_avatar,
    validate_avatar_upload,
)
from app.services.rate_limit import check_registration_rate_limit
from app.services.turnstile import verify_turnstile_token
from app.tasks.queue import enqueue_job

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=UserListResponse, include_in_schema=False)
@router.get("", response_model=UserListResponse)
async def list_users(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[UserSortParams, Depends()],
    search: Annotated[
        str | None, Query(description="Search users by username (partial, case-insensitive match)")
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    """
    List users with pagination and optional username search.
    """
    query = select(Users).options(
        selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
    )

    # Apply search filter
    if search:
        # Case-insensitive match - include substring matches
        query = query.where(func.lower(Users.username).like(f"%{search.lower()}%"))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Map sort_by to database column
    sort_column_map = {
        "user_id": Users.user_id,
        "username": Users.username,
        "date_joined": Users.date_joined,
        "last_login": Users.last_login,
        "image_posts": Users.image_posts,
        "posts": Users.posts,
        "favorites": Users.favorites,
    }
    sort_column = sort_column_map.get(sorting.sort_by, Users.user_id)
    sort_func = desc if sorting.sort_order == "DESC" else asc

    # Apply sorting. If a search is present, order by relevance first
    if search:
        s_lower = search.lower()
        relevance = case(
            (
                func.lower(Users.username) == s_lower,
                0,
            ),
            (
                func.lower(Users.username).like(f"{s_lower}%"),
                1,
            ),
            else_=2,
        )
        query = query.order_by(asc(relevance), sort_func(sort_column))  # type: ignore[arg-type]
    else:
        query = query.order_by(sort_func(sort_column))  # type: ignore[arg-type]

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


@router.get("/me", response_model=UserPrivateResponse)
async def get_current_user_profile(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserPrivateResponse:
    """
    Get the profile of the currently authenticated user.

    This includes private settings like email, email_verified, email_pm_pref,
    and the user's permissions (cached for performance).
    """
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == current_user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch user permissions (cached)
    permissions = await get_cached_user_permissions(db, redis_client, current_user_id)

    # Convert to response model and add permissions
    response = UserPrivateResponse.model_validate(user)
    response.permissions = sorted(permissions)
    return response


@router.patch("/me", response_model=UserPrivateResponse)
async def update_current_user_profile(
    user_data: UserUpdate,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserPrivateResponse:
    """
    Update the profile of the currently authenticated user.

    All fields are optional. Only provided fields will be updated.
    Returns the updated profile including private settings.
    """
    user = await _update_user_profile(current_user_id, user_data, current_user_id, db)
    return UserPrivateResponse.model_validate(user)


@router.post("/me/avatar", response_model=UserResponse)
async def upload_current_user_avatar(
    avatar: Annotated[UploadFile, File(description="Avatar image file")],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Upload avatar for the currently authenticated user.

    Accepts JPG, PNG, or GIF (animated supported). Images are resized to fit
    within 200x200 pixels while preserving aspect ratio. Maximum file size is 1MB.
    """
    return await _upload_avatar(current_user_id, avatar, db)


@router.post("/{user_id}/avatar", response_model=UserResponse)
async def upload_user_avatar(
    user_id: Annotated[int, Path(description="User ID")],
    avatar: Annotated[UploadFile, File(description="Avatar image file")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Upload avatar for a specified user.

    - Regular users can only upload their own avatar (user_id must match their ID)
    - Users with USER_EDIT_PROFILE permission can upload avatars for any user

    Accepts JPG, PNG, or GIF (animated supported). Images are resized to fit
    within 200x200 pixels while preserving aspect ratio. Maximum file size is 1MB.
    """
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user's avatar",
        )

    return await _upload_avatar(user_id, avatar, db)


async def _upload_avatar(
    user_id: int,
    avatar: UploadFile,
    db: AsyncSession,
) -> UserResponse:
    """
    Internal function to handle avatar upload.

    Args:
        user_id: ID of user to update
        avatar: Uploaded avatar file
        db: Database session

    Returns:
        Updated user response
    """
    # Get user
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Save old avatar filename for potential cleanup
    old_avatar = user.avatar

    # Save uploaded file to temp location for validation
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_path = FilePath(temp_file.name)
        content = await avatar.read()
        temp_file.write(content)

    try:
        # Validate the upload
        validate_avatar_upload(avatar, temp_path)

        # Resize and process
        processed_content, ext = resize_avatar(temp_path)

        # Save to permanent storage
        new_filename = save_avatar(processed_content, ext)

        # Update user record
        user.avatar = new_filename
        await db.commit()
        await db.refresh(user)

        # Clean up old avatar if orphaned (after commit to ensure new one is saved)
        if old_avatar and old_avatar != new_filename:
            await delete_avatar_if_orphaned(old_avatar, db)

    finally:
        # Clean up temp file
        temp_path.unlink(missing_ok=True)

    return UserResponse.model_validate(user)


@router.delete("/me/avatar", response_model=UserResponse)
async def delete_current_user_avatar(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Remove avatar for the currently authenticated user.
    """
    return await _delete_avatar(current_user_id, db)


@router.get("/me/warnings", response_model=UserWarningsResponse)
async def get_current_user_warnings(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> UserWarningsResponse:
    """
    Get unacknowledged warnings and suspension notices for the current user.

    Returns warnings and suspension notices that have not been acknowledged.
    The frontend should display these to the user and call the acknowledge
    endpoint once they've been viewed.
    """
    result = await db.execute(
        select(UserSuspensions)
        .where(UserSuspensions.user_id == current_user_id)  # type: ignore[arg-type]
        .where(UserSuspensions.acknowledged_at.is_(None))  # type: ignore[union-attr]
        .where(UserSuspensions.action != SuspensionAction.REACTIVATED)  # type: ignore[arg-type]
        .order_by(desc(UserSuspensions.actioned_at))  # type: ignore[arg-type]
    )
    suspensions = result.scalars().all()

    return UserWarningsResponse(
        items=[UserWarningResponse.model_validate(s) for s in suspensions],
        count=len(suspensions),
    )


@router.post("/me/warnings/acknowledge", response_model=AcknowledgeWarningsResponse)
async def acknowledge_warnings(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> AcknowledgeWarningsResponse:
    """
    Acknowledge all unacknowledged warnings and suspension notices.

    Sets acknowledged_at to the current timestamp for all unacknowledged
    warnings/suspensions belonging to the current user.
    """
    result = await db.execute(
        select(UserSuspensions)
        .where(UserSuspensions.user_id == current_user_id)  # type: ignore[arg-type]
        .where(UserSuspensions.acknowledged_at.is_(None))  # type: ignore[union-attr]
        .where(UserSuspensions.action != SuspensionAction.REACTIVATED)  # type: ignore[arg-type]
    )
    suspensions = result.scalars().all()

    now = datetime.now(UTC)
    for suspension in suspensions:
        suspension.acknowledged_at = now

    await db.commit()

    count = len(suspensions)
    message = f"Acknowledged {count} warning(s)" if count > 0 else "No warnings to acknowledge"

    return AcknowledgeWarningsResponse(
        acknowledged_count=count,
        message=message,
    )


@router.delete("/{user_id}/avatar", response_model=UserResponse)
async def delete_user_avatar(
    user_id: Annotated[int, Path(description="User ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Remove avatar for a specified user.

    - Regular users can only delete their own avatar (user_id must match their ID)
    - Users with USER_EDIT_PROFILE permission can delete avatars for any user
    """
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user's avatar",
        )

    return await _delete_avatar(user_id, db)


async def _delete_avatar(
    user_id: int,
    db: AsyncSession,
) -> UserResponse:
    """
    Internal function to handle avatar deletion.

    Args:
        user_id: ID of user to update
        db: Database session

    Returns:
        Updated user response
    """
    # Get user
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Save old avatar filename for cleanup
    old_avatar = user.avatar

    # Clear avatar
    user.avatar = ""
    await db.commit()
    await db.refresh(user)

    # Clean up old avatar file if orphaned
    if old_avatar:
        await delete_avatar_if_orphaned(old_avatar, db)

    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user_profile(
    user_id: Annotated[int, Path(description="User ID to update")],
    user_data: UserUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Update a user's profile.

    - Regular users can only update their own profile (user_id must match their ID)
    - Users with USER_EDIT_PROFILE permission can update any user's profile

    All fields are optional. Only provided fields will be updated.
    """
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    user = await _update_user_profile(user_id, user_data, current_user.user_id, db)
    return UserResponse.model_validate(user)


async def _update_user_profile(
    user_id: int,
    user_data: UserUpdate,
    current_user_id: int,
    db: AsyncSession,
) -> Users:
    """
    Internal function to handle user profile updates.

    Args:
        user_id: ID of user to update
        user_data: Update data
        current_user_id: ID of user making the request (for email uniqueness check)
        db: Database session

    Returns:
        Updated user response (as UserResponse; caller may wrap as UserPrivateResponse)
    """
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update only provided fields
    update_data = user_data.model_dump(exclude_unset=True)

    # Handle timezone conversion (string to Decimal)
    if "timezone" in update_data:
        from decimal import Decimal

        update_data["timezone"] = Decimal(update_data["timezone"])

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

    # Update user fields
    # NOTE: Input sanitization is now handled by schema validators (UserUpdate)
    # which escape HTML to prevent XSS attacks. We no longer normalize here.
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    # Return the DB model instance so callers can serialize to either
    # public or private response schemas depending on the endpoint.
    return user


@router.get("/{user_id}/images", response_model=ImageDetailedListResponse)
async def get_user_images(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageDetailedListResponse:
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
    query = (
        select(Images)
        .options(
            selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar),  # type: ignore[arg-type]
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .where(Images.user_id == user_id)  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = sorting.sort_by.get_column(Images)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageDetailedResponse.model_validate(img) for img in images],
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: Annotated[int, Path(description="User ID")],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get user profile information.
    """
    user_result = await db.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)  # type: ignore[arg-type]
        )
        .where(Users.user_id == user_id)  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)


@router.get("/{user_id}/favorites", response_model=ImageDetailedListResponse)
async def get_user_favorites(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
) -> ImageDetailedListResponse:
    """
    Get all images favorited by a specific user.
    """
    # Verify user exists
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's favorite images
    query = (
        select(Images)
        .options(
            selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar),  # type: ignore[arg-type]
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .join(Favorites)
        .where(Favorites.user_id == user_id)  # type: ignore[arg-type]
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = sorting.sort_by.get_column(Images)
    if sorting.sort_order == "DESC":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))

    # Apply pagination
    query = query.offset(pagination.offset).limit(pagination.per_page)

    # Execute
    result = await db.execute(query)
    images = result.scalars().all()

    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[ImageDetailedResponse.model_validate(img) for img in images],
    )


@router.post("/", response_model=UserCreateResponse, include_in_schema=False)
@router.post("", response_model=UserCreateResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserCreateResponse:
    """
    Create a new user with bot protection.
    """
    # 1. Honeypot check (fail fast)
    if user_data.website_url:
        # Bot detected! Silently reject without revealing honeypot
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid registration request.",
        )

    # 2. Rate limiting
    ip_address = get_client_ip(request)
    await check_registration_rate_limit(ip_address, redis_client)

    # 3. Turnstile verification
    await verify_turnstile_token(user_data.turnstile_token, ip_address)

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
    if existing_user.scalar():
        raise HTTPException(status_code=409, detail="Username or email already exists")

    # Check the password strength
    is_valid, error_message = validate_password_strength(user_data.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    # Generate email verification token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    new_user = Users(
        username=user_data.username,
        password=get_password_hash(user_data.password),
        password_type="bcrypt",  # Mark as bcrypt password
        salt="",  # Legacy field - empty for bcrypt users
        email=user_data.email,
        active=1,  # New users are active by default (can login)
        admin=0,  # New users are not admin
        email_verified=False,  # Not verified yet
        email_verification_token=token_hash,
        email_verification_sent_at=datetime.now(UTC),
        email_verification_expires_at=datetime.now(UTC) + timedelta(hours=24),
        # Other fields use model defaults
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Queue verification email via ARQ (reliable, retries on failure)
    await enqueue_job("send_verification_email_job", user_id=new_user.user_id, token=raw_token)

    return UserCreateResponse.model_validate(new_user)

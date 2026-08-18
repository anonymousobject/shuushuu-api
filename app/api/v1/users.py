"""
Users API endpoints
"""

import hashlib
import re
import secrets
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path as FilePath
from typing import Annotated, Any

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
from sqlalchemy import asc, case, delete, desc, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.dependencies import (
    ImageSortParams,
    PaginationParams,
    UserRatingsSortParams,
    UserSortParams,
)
from app.config import ImageStatus, SuspensionAction, TagType, settings
from app.core.auth import (
    get_client_ip,
    get_current_user,
    get_current_user_id,
    get_optional_current_user,
)
from app.core.database import get_db
from app.core.db_retry import retry_on_transient_conflict
from app.core.logging import get_logger
from app.core.permissions import Permission, has_permission
from app.core.r2_client import get_r2_storage
from app.core.redis import get_redis
from app.core.security import RedactedStr, get_password_hash, validate_password_strength
from app.core.user_loader import image_uploader_load
from app.models import Favorites, ImageRatings, Images, TagLinks, Tags, Users
from app.models.character_source_link import CharacterSourceLinks
from app.models.character_source_link_picture import CharacterSourceLinkPictures
from app.models.permissions import UserGroups
from app.models.refresh_token import RefreshTokens
from app.models.user_favorite import UserFavoriteLinks, UserFavoriteTags
from app.models.user_suspension import UserSuspensions
from app.models.user_tag_affinity import UserTagAffinity
from app.schemas.image import (
    ImageDetailedListResponse,
    ImageDetailedResponse,
    ImageWithRatingResponse,
    UserRatingsListResponse,
    thumbnail_url_for,
)
from app.schemas.tag import LinkedTag, LinkPictureInfo
from app.schemas.taste_profile import TasteProfileResponse, TasteProfileSummary, TasteProfileTag
from app.schemas.user import (
    AcknowledgeWarningsResponse,
    FavoriteCharacterEntry,
    FavoriteOrderUpdate,
    FavoriteTagCreate,
    FavoriteTagEntry,
    UserCreate,
    UserCreateResponse,
    UserFavoriteTagsResponse,
    UserListResponse,
    UserPrivateResponse,
    UserResponse,
    UserUpdate,
    UserWarningResponse,
    UserWarningsResponse,
)
from app.services.avatar import (
    avatar_content_type,
    delete_avatar_if_orphaned,
    resize_avatar,
    save_avatar,
    validate_avatar_upload,
)
from app.services.feeds import TAG_TYPE_NAME
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES
from app.services.rate_limit import check_registration_rate_limit
from app.services.tag_context import stamp_context_sources
from app.services.turnstile import verify_turnstile_token
from app.services.user import build_user_private_response
from app.tasks.queue import enqueue_job

logger = get_logger(__name__)

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
        "last_active": Users.last_active,
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
    response = await build_user_private_response(db, redis_client, user_id=current_user_id)
    if response is None:
        raise HTTPException(status_code=404, detail="User not found")
    return response


@router.patch("/me", response_model=UserPrivateResponse)
async def update_current_user_profile(
    user_data: UserUpdate,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserPrivateResponse:
    """
    Update the profile of the currently authenticated user.

    All fields are optional. Only provided fields will be updated.
    Returns the updated profile including private settings.

    Note: user_title can only be updated by users with USER_EDIT_PROFILE permission.
    """
    # Check if user has permission to edit profiles (required for user_title)
    has_edit_permission = await has_permission(
        db, current_user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    user = await _update_user_profile(
        current_user_id, user_data, current_user_id, db, has_edit_permission
    )
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

    # Capture old avatar state BEFORE any mutation. The orphan helper needs
    # the old `avatar_in_r2` bit to know whether to issue an R2 delete; if we
    # captured it after the commit below, the new value would have already
    # overwritten the old one.
    old_avatar = user.avatar
    old_in_r2 = user.avatar_in_r2

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

        # Best-effort dual-write to R2. Failure is non-fatal: the local file
        # is the source of truth during the dual-write phase. A failed upload
        # leaves avatar_in_r2=False and the URL falls back to local.
        new_in_r2 = False
        if settings.R2_ENABLED:
            try:
                r2 = get_r2_storage()
                await r2.upload_bytes(
                    bucket=settings.R2_PUBLIC_BUCKET,
                    key=f"avatars/{new_filename}",
                    body=processed_content,
                    content_type=avatar_content_type(ext),
                )
                new_in_r2 = True
                logger.info(
                    "avatar_r2_uploaded",
                    user_id=user_id,
                    key=f"avatars/{new_filename}",
                )
            except Exception as e:
                logger.warning(
                    "avatar_r2_upload_failed",
                    user_id=user_id,
                    key=f"avatars/{new_filename}",
                    error=type(e).__name__,
                    error_msg=str(e),
                )

        # Update user record
        user.avatar = new_filename
        user.avatar_in_r2 = new_in_r2
        await db.commit()
        await db.refresh(user)

        # Clean up old avatar if orphaned (after commit to ensure new one is
        # saved). The orphan check counts users referencing old_avatar AFTER
        # the commit — so a same-MD5 re-upload (new_filename == old_avatar)
        # finds the user themselves still references it (count >= 1) and
        # safely skips deletion.
        if old_avatar and old_avatar != new_filename:
            await delete_avatar_if_orphaned(old_avatar, old_in_r2, db)

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


@router.get("/me/taste-profile", response_model=TasteProfileResponse)
async def get_taste_profile(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
) -> TasteProfileResponse:
    """
    The logged-in user's taste profile (private, owner-only).

    Reads the precomputed user_tag_affinity table (refreshed nightly).
    top_tags applies the TASTE_DISPLAY_MIN_LIFT floor so popularity-only tags
    (e.g. "long hair" at lift ~1.3) don't crowd out actual taste; rated_high /
    rated_low are the largest positive / negative per-user-centered rating
    deltas with rating support.
    """
    # profile_ready = any rows exist. Do NOT key this on updated_at: ORM
    # inserts (tests) send explicit NULL there; only the refresh job's raw
    # INSERT…SELECT gets the server default.
    has_rows = (
        await db.execute(
            select(UserTagAffinity.user_id)  # type: ignore[call-overload]
            .where(UserTagAffinity.user_id == current_user_id)
            .limit(1)
        )
    ).first()
    if has_rows is None:
        return TasteProfileResponse(profile_ready=False)
    updated_at = (
        await db.execute(
            select(func.max(UserTagAffinity.updated_at)).where(
                UserTagAffinity.user_id == current_user_id  # type: ignore[arg-type]
            )
        )
    ).scalar()

    def _mk(row: Any) -> TasteProfileTag:
        aff, title, ttype = row
        return TasteProfileTag(
            tag_id=aff.tag_id,
            title=title,
            type=ttype,
            type_name=TAG_TYPE_NAME.get(ttype, "Tag"),
            pool_cnt=aff.pool_cnt,
            fav_count=aff.fav_count,
            upload_count=aff.upload_count,
            rated_count=aff.rated_count,
            rating_avg=aff.rating_avg,
            lift=aff.lift,
            rating_delta=aff.rating_delta,
            affinity=aff.affinity,
        )

    base = (
        select(UserTagAffinity, Tags.title, Tags.type)  # type: ignore[call-overload]
        .join(Tags, Tags.tag_id == UserTagAffinity.tag_id)
        .where(UserTagAffinity.user_id == current_user_id)
    )
    top_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.pool_cnt >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.lift >= settings.TASTE_DISPLAY_MIN_LIFT,  # type: ignore[operator]
                UserTagAffinity.affinity > 0,
            )
            .order_by(desc(UserTagAffinity.affinity))  # type: ignore[arg-type]
            .limit(40)
        )
    ).all()
    high_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.rated_count >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.rating_delta > 0,  # type: ignore[operator]
            )
            .order_by(desc(UserTagAffinity.rating_delta))  # type: ignore[arg-type]
            .limit(10)
        )
    ).all()
    low_rows = (
        await db.execute(
            base.where(
                UserTagAffinity.rated_count >= settings.TASTE_MIN_SUPPORT,
                UserTagAffinity.rating_delta < 0,  # type: ignore[operator]
            )
            .order_by(UserTagAffinity.rating_delta)
            .limit(10)
        )
    ).all()

    pool_size = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT image_id FROM favorites WHERE user_id = :u "
                "UNION SELECT image_id FROM images WHERE user_id = :u) p"
            ),
            {"u": current_user_id},
        )
    ).scalar() or 0
    rated_row = (
        await db.execute(
            text("SELECT COUNT(*) c, AVG(rating) m FROM image_ratings WHERE user_id = :u"),
            {"u": current_user_id},
        )
    ).one()

    return TasteProfileResponse(
        profile_ready=True,
        summary=TasteProfileSummary(
            pool_size=pool_size,
            rated_total=rated_row.c or 0,
            mean_rating=float(rated_row.m) if rated_row.m is not None else None,
            updated_at=updated_at,
        ),
        top_tags=[_mk(r) for r in top_rows],
        rated_high=[_mk(r) for r in high_rows],
        rated_low=[_mk(r) for r in low_rows],
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
    old_in_r2 = user.avatar_in_r2

    # Clear avatar
    user.avatar = ""
    user.avatar_in_r2 = False
    await db.commit()
    await db.refresh(user)

    # Clean up old avatar file if orphaned
    if old_avatar:
        await delete_avatar_if_orphaned(old_avatar, old_in_r2, db)

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

    user = await _update_user_profile(
        user_id, user_data, current_user.user_id, db, has_edit_permission
    )
    return UserResponse.model_validate(user)


async def _update_user_profile(
    user_id: int,
    user_data: UserUpdate,
    current_user_id: int,
    db: AsyncSession,
    has_edit_permission: bool = False,
) -> Users:
    """
    Internal function to handle user profile updates.

    Args:
        user_id: ID of user to update
        user_data: Update data
        current_user_id: ID of user making the request (for email uniqueness check)
        db: Database session
        has_edit_permission: Whether the current user has USER_EDIT_PROFILE permission

    Returns:
        Updated user response (as UserResponse; caller may wrap as UserPrivateResponse)
    """

    # Concurrent PATCHes of the same users row can hit a snapshot conflict at
    # commit (see app/core/db_retry.py), so the whole fetch-apply-commit unit
    # is retried. Everything below re-derives its state per attempt — notably
    # update_data, which the password branch pops from and must not be shared
    # across attempts.
    async def _apply() -> Users:
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

        # user_title can only be updated by users with USER_EDIT_PROFILE permission
        if "user_title" in update_data and not has_edit_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only moderators can update user titles",
            )

        # maximgperday can only be updated by users with USER_EDIT_PROFILE permission
        if "maximgperday" in update_data and not has_edit_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only moderators can update user upload limits",
            )

        # Handle password separately with validation and hashing.
        # Self-service password changes must go through POST /auth/change-password,
        # which verifies the current password and revokes existing sessions; PATCH
        # would silently bypass both. Moderators may still set passwords here.
        if "password" in update_data:
            if not has_edit_permission:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Use /api/v1/auth/change-password to change your own password",
                )
            password = update_data.pop("password")
            is_valid, error_message = validate_password_strength(password)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_message)
            user.password = get_password_hash(password)
            user.password_type = "bcrypt"
            # A forced reset usually responds to a compromised account: revoke the
            # target's sessions so holders of the old credentials are logged out.
            await db.execute(
                delete(RefreshTokens).where(RefreshTokens.user_id == user.user_id)  # type: ignore[arg-type]
            )

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

    return await retry_on_transient_conflict(db, _apply, what="user_profile_update")


@router.get("/{user_id}/images", response_model=ImageDetailedListResponse)
async def get_user_images(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
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
            image_uploader_load(),
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .where(Images.user_id == user_id)  # type: ignore[arg-type]
    )

    # Visibility filtering: anonymous see only public statuses, authenticated
    # users also see their own images regardless of status
    if current_user is not None:
        query = query.where(
            or_(
                Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                Images.user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )
    else:
        query = query.where(Images.status.in_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]

    if current_user is not None and current_user.hide_reposts == 1:
        query = query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

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
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
    current_user: Users | None = Depends(get_optional_current_user),
) -> UserResponse:
    """
    Get user profile information.

    The maximgperday field is only visible to:
    - The user viewing their own profile
    - Users with USER_EDIT_PROFILE permission (moderators/admins)
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

    response = UserResponse.model_validate(user)

    # maximgperday is only visible to self or users with edit permission
    # Default to hidden (None)
    response.maximgperday = None
    if current_user and current_user.user_id is not None:
        is_self = current_user.user_id == user_id
        has_edit_permission = await has_permission(
            db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
        )
        if is_self or has_edit_permission:
            response.maximgperday = user.maximgperday

    return response


@router.get("/{user_id}/favorites", response_model=ImageDetailedListResponse)
async def get_user_favorites(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[ImageSortParams, Depends()],
    db: AsyncSession = Depends(get_db),
    current_user: Users | None = Depends(get_optional_current_user),
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
            image_uploader_load(),
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .join(Favorites)
        .where(Favorites.user_id == user_id)  # type: ignore[arg-type]
    )

    # Visibility filtering: anonymous see only public statuses, authenticated
    # users also see their own images regardless of status
    if current_user is not None:
        query = query.where(
            or_(
                Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                Images.user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )
    else:
        query = query.where(Images.status.in_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]

    if current_user is not None and current_user.hide_reposts == 1:
        query = query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

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


def _character_link_select() -> Any:
    """Base 18-column select for one favorited character combo's display row.

    Typed ``Any``, not ``Select[Any]``: the 18-column ``select()`` call below
    already fails mypy's overload resolution (see its own ignore comment) and
    resolves to ``Any`` at that point, exactly as it did inline in Task 1 --
    an explicit ``Select[Any]`` return annotation would make mypy check the
    chained ``.join()``/``.where()``/``.order_by()`` calls at every call site
    for real, which is a much bigger surface than this helper is worth.

    Shared by the grouped GET (many rows, one query) and
    ``_favorite_character_entry`` (one row, filtered by link_id) — the join
    shape lives here exactly once.
    """
    char_tags = aliased(Tags)
    src_tags = aliased(Tags)
    return (
        select(  # type: ignore[call-overload, misc]
            UserFavoriteLinks.link_id,
            UserFavoriteLinks.position,
            char_tags.tag_id,
            char_tags.title,
            char_tags.type,
            char_tags.usage_count,
            src_tags.tag_id,
            src_tags.title,
            src_tags.type,
            src_tags.usage_count,
            CharacterSourceLinkPictures.image_id,
            CharacterSourceLinkPictures.crop_x,
            CharacterSourceLinkPictures.crop_y,
            CharacterSourceLinkPictures.crop_w,
            CharacterSourceLinkPictures.crop_h,
            Images.filename,
            Images.status,
            Images.r2_location,
        )
        .join(CharacterSourceLinks, CharacterSourceLinks.id == UserFavoriteLinks.link_id)
        .join(char_tags, char_tags.tag_id == CharacterSourceLinks.character_tag_id)
        .join(src_tags, src_tags.tag_id == CharacterSourceLinks.source_tag_id)
        .outerjoin(
            CharacterSourceLinkPictures,
            CharacterSourceLinkPictures.link_id == CharacterSourceLinks.id,
        )
        .outerjoin(Images, Images.image_id == CharacterSourceLinkPictures.image_id)
    )


def _character_entry_from_row(row: Any) -> FavoriteCharacterEntry:
    """Turn one ``_character_link_select`` row into its response entry."""
    (
        link_id,
        position,
        c_id,
        c_title,
        c_type,
        c_usage,
        s_id,
        s_title,
        s_type,
        s_usage,
        pic_image_id,
        crop_x,
        crop_y,
        crop_w,
        crop_h,
        filename,
        status,
        r2_location,
    ) = row
    picture = None
    # Re-checked at read time: a deactivated image must not leak through.
    if pic_image_id is not None and status in PUBLIC_IMAGE_STATUSES:
        picture = LinkPictureInfo(
            image_id=pic_image_id,
            thumbnail_url=thumbnail_url_for(filename, status, r2_location),
            crop_x=crop_x,
            crop_y=crop_y,
            crop_w=crop_w,
            crop_h=crop_h,
        )
    return FavoriteCharacterEntry(
        link_id=link_id,
        position=position,
        character=LinkedTag(tag_id=c_id, title=c_title, type=c_type, usage_count=c_usage),
        source=LinkedTag(tag_id=s_id, title=s_title, type=s_type, usage_count=s_usage),
        picture=picture,
    )


async def _favorite_character_entry(
    db: AsyncSession, user_id: int, link_id: int
) -> FavoriteCharacterEntry | None:
    """Single-row echo of one favorited combo — shares the grouped GET's join."""
    result = await db.execute(
        _character_link_select().where(
            UserFavoriteLinks.user_id == user_id,
            UserFavoriteLinks.link_id == link_id,
        )
    )
    row = result.first()
    if row is None:
        return None
    return _character_entry_from_row(row)


@router.get("/{user_id}/favorite-tags", response_model=UserFavoriteTagsResponse)
async def get_user_favorite_tags(
    user_id: Annotated[int, Path(description="User ID")],
    db: AsyncSession = Depends(get_db),
) -> UserFavoriteTagsResponse:
    """
    A user's public profile favorites, grouped and position-ordered.

    Characters are per (character, source) link and carry the link's
    picture when one is set and its image is still publicly visible —
    the same read-time re-check the tag-detail embed does.
    """
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    link_rows = await db.execute(
        _character_link_select()
        .where(UserFavoriteLinks.user_id == user_id)
        .order_by(UserFavoriteLinks.position)
    )
    characters = [_character_entry_from_row(row) for row in link_rows.all()]

    tag_rows = await db.execute(
        select(  # type: ignore[call-overload]
            UserFavoriteTags.position,
            Tags.tag_id,
            Tags.title,
            Tags.type,
            Tags.usage_count,
        )
        .join(Tags, Tags.tag_id == UserFavoriteTags.tag_id)
        .where(UserFavoriteTags.user_id == user_id)
        .order_by(UserFavoriteTags.position)
    )
    sources: list[FavoriteTagEntry] = []
    artists: list[FavoriteTagEntry] = []
    for position, tag_id, title, tag_type, usage_count in tag_rows.all():
        entry = FavoriteTagEntry(
            position=position,
            tag=LinkedTag(tag_id=tag_id, title=title, type=tag_type, usage_count=usage_count),
        )
        (sources if tag_type == TagType.SOURCE else artists).append(entry)

    return UserFavoriteTagsResponse(characters=characters, sources=sources, artists=artists)


FAVORITES_PER_CATEGORY_CAP = 20


@router.post("/me/favorite-tags", status_code=201)
async def add_favorite_tag(
    body: FavoriteTagCreate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> FavoriteCharacterEntry | FavoriteTagEntry:
    """Add a favorite (source/artist tag by tag_id, character combo by link_id)."""
    if (body.tag_id is None) == (body.link_id is None):
        raise HTTPException(status_code=400, detail="Provide exactly one of tag_id or link_id")
    assert current_user.user_id is not None

    if body.tag_id is not None:
        tag_result = await db.execute(
            select(Tags).where(Tags.tag_id == body.tag_id)  # type: ignore[arg-type]
        )
        tag = tag_result.scalar_one_or_none()
        if not tag:
            raise HTTPException(status_code=404, detail="Tag not found")
        if tag.type not in (TagType.SOURCE, TagType.ARTIST):
            raise HTTPException(
                status_code=400,
                detail="Only Source or Artist tags can be favorited directly "
                "(characters are favorited per source combo)",
            )
        if tag.alias_of is not None:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot favorite an alias tag; use the canonical tag (id: {tag.alias_of})",
            )
        # Cap counts only THIS tag-type's favorites (sources and artists
        # are independent categories sharing one table).
        count = (
            await db.execute(
                select(func.count())
                .select_from(UserFavoriteTags)
                .join(Tags, Tags.tag_id == UserFavoriteTags.tag_id)  # type: ignore[arg-type]
                .where(
                    UserFavoriteTags.user_id == current_user.user_id,  # type: ignore[arg-type]
                    Tags.type == tag.type,  # type: ignore[arg-type]
                )
            )
        ).scalar() or 0
        if count >= FAVORITES_PER_CATEGORY_CAP:
            raise HTTPException(
                status_code=400,
                detail=f"Favorite limit reached ({FAVORITES_PER_CATEGORY_CAP} per category)",
            )
        max_pos = (
            await db.execute(
                select(func.max(UserFavoriteTags.position))
                .join(Tags, Tags.tag_id == UserFavoriteTags.tag_id)  # type: ignore[arg-type]
                .where(
                    UserFavoriteTags.user_id == current_user.user_id,  # type: ignore[arg-type]
                    Tags.type == tag.type,  # type: ignore[arg-type]
                )
            )
        ).scalar()
        position = 0 if max_pos is None else max_pos + 1
        db.add(
            UserFavoriteTags(user_id=current_user.user_id, tag_id=body.tag_id, position=position)
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Already favorited") from None
        return FavoriteTagEntry(
            position=position,
            tag=LinkedTag(
                tag_id=tag.tag_id or 0,
                title=tag.title,
                type=tag.type,
                usage_count=tag.usage_count,
            ),
        )

    # link_id path
    assert body.link_id is not None  # guaranteed by the XOR check above
    link_result = await db.execute(
        select(CharacterSourceLinks).where(
            CharacterSourceLinks.id == body.link_id  # type: ignore[arg-type]
        )
    )
    link = link_result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    count = (
        await db.execute(
            select(func.count())
            .select_from(UserFavoriteLinks)
            .where(UserFavoriteLinks.user_id == current_user.user_id)  # type: ignore[arg-type]
        )
    ).scalar() or 0
    if count >= FAVORITES_PER_CATEGORY_CAP:
        raise HTTPException(
            status_code=400,
            detail=f"Favorite limit reached ({FAVORITES_PER_CATEGORY_CAP} per category)",
        )
    max_pos = (
        await db.execute(
            select(func.max(UserFavoriteLinks.position)).where(
                UserFavoriteLinks.user_id == current_user.user_id  # type: ignore[arg-type]
            )
        )
    ).scalar()
    position = 0 if max_pos is None else max_pos + 1
    db.add(UserFavoriteLinks(user_id=current_user.user_id, link_id=body.link_id, position=position))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already favorited") from None

    # Echo in the GET's entry shape — reuse the grouped GET's join for one row.
    entry = await _favorite_character_entry(db, current_user.user_id, body.link_id)
    assert entry is not None
    return entry


@router.delete("/me/favorite-tags/tag/{tag_id}", status_code=204)
async def remove_favorite_tag(
    tag_id: Annotated[int, Path()],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a favorited source/artist tag."""
    result = await db.execute(
        select(UserFavoriteTags).where(
            UserFavoriteTags.user_id == current_user.user_id,  # type: ignore[arg-type]
            UserFavoriteTags.tag_id == tag_id,  # type: ignore[arg-type]
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not favorited")
    await db.delete(row)
    await db.commit()


@router.delete("/me/favorite-tags/link/{link_id}", status_code=204)
async def remove_favorite_link(
    link_id: Annotated[int, Path()],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a favorited character combo."""
    result = await db.execute(
        select(UserFavoriteLinks).where(
            UserFavoriteLinks.user_id == current_user.user_id,  # type: ignore[arg-type]
            UserFavoriteLinks.link_id == link_id,  # type: ignore[arg-type]
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not favorited")
    await db.delete(row)
    await db.commit()


@router.put("/me/favorite-tags/order", status_code=204)
async def reorder_favorite_tags(
    body: FavoriteOrderUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Atomically rewrite one category's positions to match the given id order."""
    assert current_user.user_id is not None
    rows: Sequence[UserFavoriteLinks] | Sequence[UserFavoriteTags]
    if body.category == "characters":
        rows = (
            (
                await db.execute(
                    select(UserFavoriteLinks).where(
                        UserFavoriteLinks.user_id == current_user.user_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        current_ids = {row.link_id for row in rows}
    else:
        wanted_type = TagType.SOURCE if body.category == "sources" else TagType.ARTIST
        rows = (
            (
                await db.execute(
                    select(UserFavoriteTags)
                    .join(Tags, Tags.tag_id == UserFavoriteTags.tag_id)  # type: ignore[arg-type]
                    .where(
                        UserFavoriteTags.user_id == current_user.user_id,  # type: ignore[arg-type]
                        Tags.type == wanted_type,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        current_ids = {row.tag_id for row in rows}

    if len(body.ids) != len(current_ids) or set(body.ids) != current_ids:
        raise HTTPException(
            status_code=400,
            detail="ids must be a permutation of the category's current favorites",
        )

    by_id = {
        (row.link_id if body.category == "characters" else row.tag_id): row  # type: ignore[union-attr]
        for row in rows
    }
    for position, entry_id in enumerate(body.ids):
        by_id[entry_id].position = position
    await db.commit()


@router.get("/{user_id}/ratings", response_model=UserRatingsListResponse)
async def get_user_ratings(
    user_id: Annotated[int, Path(description="User ID")],
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[UserRatingsSortParams, Depends()],
    min_rating: Annotated[
        int | None, Query(ge=1, le=10, description="Only ratings at or above this value")
    ] = None,
    max_rating: Annotated[
        int | None, Query(ge=1, le=10, description="Only ratings at or below this value")
    ] = None,
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
    current_user: Users | None = Depends(get_optional_current_user),
) -> UserRatingsListResponse:
    """
    Get all images a user has rated, with the rating they gave.

    Private: visible only to the user themselves and to moderators holding
    USER_EDIT_PROFILE. Moderators see every rated image regardless of status,
    because an audit for rating-bombing needs the deactivated ones.

    `min_rating` / `max_rating` filter on the subject user's own score. Note that
    `/images?min_rating=` means something different — the image's *average*.
    """
    # Existence first: user existence is already public via GET /users/{id}, so a
    # 404 ahead of the gate leaks nothing new. Matches /users/{id}/favorites.
    user_result = await db.execute(select(Users).where(Users.user_id == user_id))  # type: ignore[arg-type]
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to view ratings",
        )

    is_self = current_user.user_id == user_id
    is_mod = await has_permission(db, current_user.id, Permission.USER_EDIT_PROFILE, redis_client)
    if not (is_self or is_mod):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own ratings",
        )

    query = (
        select(
            Images,
            ImageRatings.rating.label("rating_value"),  # type: ignore[attr-defined]
            ImageRatings.date.label("rated_at"),  # type: ignore[union-attr]
        )
        .join(ImageRatings, ImageRatings.image_id == Images.image_id)  # type: ignore[arg-type]
        .where(ImageRatings.user_id == user_id)  # type: ignore[arg-type]
        .options(
            image_uploader_load(),
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
    )

    # Moderators bypass both filters — hiding deactivated images would hide the
    # evidence a rating-bombing audit is looking for.
    if not is_mod:
        query = query.where(
            or_(
                Images.status.in_(PUBLIC_IMAGE_STATUSES),  # type: ignore[attr-defined]
                Images.user_id == current_user.user_id,  # type: ignore[arg-type]
            )
        )
        if current_user.hide_reposts == 1:
            query = query.where(Images.status != ImageStatus.REPOST)  # type: ignore[arg-type]

    if min_rating is not None:
        query = query.where(ImageRatings.rating >= min_rating)  # type: ignore[arg-type]
    if max_rating is not None:
        query = query.where(ImageRatings.rating <= max_rating)  # type: ignore[arg-type]

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_columns: dict[str, Any] = {
        "image_id": Images.image_id,
        "rating": ImageRatings.rating,
        "rated_at": ImageRatings.date,
    }
    sort_column = sort_columns[sorting.sort_by]
    primary = desc(sort_column) if sorting.sort_order == "DESC" else asc(sort_column)
    # Tiebreaker: `rated_at` ties on 98% of rows and `rating` has ten distinct
    # values, so without this pagination would return unstable pages.
    tiebreaker: list[Any] = (
        [] if sorting.sort_by == "image_id" else [desc(Images.image_id)]  # type: ignore[arg-type]
    )
    query = (
        query.order_by(primary, *tiebreaker).offset(pagination.offset).limit(pagination.per_page)
    )

    rows = (await db.execute(query)).all()

    # Favorite status reflects the *viewer's* favorites, not the ratings subject's —
    # a mod auditing someone else's ratings sees their own hearts, not the subject's.
    page_ids = [image.image_id for image, _, _ in rows]
    favorited_ids: set[int] = set()
    if page_ids:
        fav_result = await db.execute(
            select(Favorites.image_id).where(  # type: ignore[call-overload]
                Favorites.user_id == current_user.user_id,
                Favorites.image_id.in_(page_ids),  # type: ignore[attr-defined]
            )
        )
        favorited_ids = set(fav_result.scalars().all())

    items: list[ImageWithRatingResponse] = []
    for image, rating_value, rated_at in rows:
        item = ImageWithRatingResponse.from_db_model(
            image,
            is_favorited=image.image_id in favorited_ids,
            can_see_reason=is_mod or image.user_id == current_user.user_id,
        )
        # from_db_model has no rating parameter, so assign after construction —
        # the same pattern list_images uses for ml_suggestion_count.
        item.subject_rating = rating_value
        item.rated_at = rated_at
        items.append(item)

    await stamp_context_sources(db, items)

    return UserRatingsListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        images=items,
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

    # Queue verification email via ARQ (reliable, retries on failure).
    # RedactedStr keeps the token usable but hides it from arq's repr-based
    # job-arg log (INFO-level, ingested by Loki).
    await enqueue_job(
        "send_verification_email_job",
        user_id=new_user.user_id,
        token=RedactedStr(raw_token),
    )

    return UserCreateResponse.model_validate(new_user)

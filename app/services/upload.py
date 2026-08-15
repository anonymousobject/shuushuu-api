"""
Image upload helpers for rate limiting, file saving, and tag linking.
"""

from datetime import UTC, datetime
from pathlib import Path as FilePath
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models import Images, TagLinks, Tags
from app.services.image_processing import calculate_md5, validate_image_file
from app.services.tag_type_flags import refresh_image_tag_type_flags

logger = get_logger(__name__)


async def get_uploads_today(user_id: int, db: AsyncSession) -> int:
    """Count how many images a user has uploaded today."""
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count())
        .select_from(Images)
        .where(
            Images.user_id == user_id,  # type: ignore[arg-type]
            Images.date_added >= start_of_day,  # type: ignore[arg-type,operator]
        )
    )
    return result.scalar() or 0


async def check_upload_rate_limit(
    user_id: int, db: AsyncSession, *, maximgperday: int | None = None
) -> None:
    """Check if user is uploading too quickly or has hit their daily limit.

    Raises HTTPException if user uploaded too recently or has reached
    their daily upload limit.
    Any bypass for admins or moderators must be implemented by the caller.
    """
    # Check daily upload limit
    if maximgperday is not None:
        uploads_today = await get_uploads_today(user_id, db)
        if uploads_today >= maximgperday:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily upload limit of {maximgperday} reached",
            )

    # Check per-upload rate limit (minimum delay between uploads)
    result = await db.execute(
        select(Images.date_added)  # type: ignore[call-overload]
        .where(Images.user_id == user_id)
        .order_by(desc(Images.date_added))  # type: ignore[arg-type]
        .limit(1)
    )
    last_upload = result.scalar_one_or_none()

    if last_upload:
        elapsed = (datetime.now(UTC) - last_upload).total_seconds()
        if elapsed < settings.UPLOAD_DELAY_SECONDS:
            wait_time = int(settings.UPLOAD_DELAY_SECONDS - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {wait_time} seconds before uploading another image",
            )


async def stage_uploaded_image(file: UploadFile, storage_path: str) -> tuple[FilePath, str, str]:
    """
    Write an upload to a staging path, validate it, and hash it.

    Returns:
        Tuple of (staged_path, extension, md5_hash)

    The file keeps its staging name until `finalize_uploaded_image` renames it:
    the permanent name embeds the image_id, which does not exist until the row
    is inserted. Splitting it this way keeps the file write and the duplicate
    checks outside the database transaction, so that transaction stays short
    enough to retry on a snapshot conflict (see app/core/db_retry.py).

    The staging name is unique per upload — two users uploading files with the
    same original name must not write to the same staging path.
    """
    # Get file extension
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    ext = FilePath(file.filename).suffix.lower().lstrip(".")

    # Create storage directories if they don't exist
    fullsize_dir = FilePath(storage_path) / "fullsize"
    fullsize_dir.mkdir(parents=True, exist_ok=True)

    staged_path = fullsize_dir / f"staged_{uuid4().hex}"
    try:
        with open(staged_path, "wb") as f:
            content = await file.read()
            if len(content) > settings.MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File size exceeds maximum of {settings.MAX_IMAGE_SIZE} bytes",
                )
            f.write(content)

        # Validate file is actually an image (security check)
        validate_image_file(file, staged_path)

        # Calculate MD5 hash
        md5_hash = calculate_md5(staged_path)

        return staged_path, ext, md5_hash
    except HTTPException:
        # Clean up staged file on validation error
        staged_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        # Clean up staged file on any error
        staged_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save image",
        ) from e


def finalize_uploaded_image(
    staged_path: FilePath, storage_path: str, image_id: int, ext: str, date_prefix: str
) -> FilePath:
    """
    Give a staged upload its permanent name: YYYY-MM-DD-{image_id}.{ext}

    `date_prefix` is supplied by the caller rather than recomputed here so the
    on-disk name always matches the filename recorded on the row, even when the
    request straddles local midnight.
    """
    fullsize_dir = FilePath(storage_path) / "fullsize"
    final_path = fullsize_dir / f"{date_prefix}-{image_id}.{ext}"
    staged_path.rename(final_path)
    return final_path


async def link_tags_to_image(
    image_id: int, tag_ids: list[int], user_id: int, db: AsyncSession
) -> None:
    """Link tags to an image (usage_count is maintained by database trigger)."""
    for tag_id in tag_ids:
        # Verify tag exists
        tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
        tag = tag_result.scalar_one_or_none()

        if not tag:
            # Skip invalid tags silently (or raise error if preferred)
            continue

        # Resolve alias to canonical tag
        resolved_id = tag.alias_of if tag.alias_of else tag_id

        # Create tag link (database trigger automatically updates tags.usage_count)
        tag_link = TagLinks(
            tag_id=resolved_id,
            image_id=image_id,
            user_id=user_id,
        )
        db.add(tag_link)

    await refresh_image_tag_type_flags(db, image_id)

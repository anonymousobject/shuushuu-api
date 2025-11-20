"""
Image upload helpers for rate limiting, file saving, and tag linking.
"""

from datetime import datetime
from pathlib import Path as FilePath

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models import Images, TagLinks, Tags
from app.services.image_processing import calculate_md5, validate_image_file

logger = get_logger(__name__)


async def check_upload_rate_limit(user_id: int, db: AsyncSession) -> None:
    """Check if user is uploading too quickly.

    Raises HTTPException if user uploaded too recently.
    Moderators/admins bypass this check.
    """
    # Get user's last upload timestamp
    result = await db.execute(
        select(Images.date_added)  # type: ignore[call-overload]
        .where(Images.user_id == user_id)
        .order_by(desc(Images.date_added))  # type: ignore[arg-type]
        .limit(1)
    )
    last_upload = result.scalar_one_or_none()

    if last_upload:
        elapsed = (datetime.now() - last_upload).total_seconds()
        if elapsed < settings.UPLOAD_DELAY_SECONDS:
            wait_time = int(settings.UPLOAD_DELAY_SECONDS - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {wait_time} seconds before uploading another image",
            )


async def save_uploaded_image(
    file: UploadFile, storage_path: str, image_id: int
) -> tuple[FilePath, str, str]:
    """
    Save uploaded image to storage with format: YYYY-MM-DD-{image_id}.{ext}

    Returns:
        Tuple of (file_path, extension, md5_hash)
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

    # Save file temporarily to validate and calculate hash
    temp_path = fullsize_dir / f"temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            if len(content) > settings.MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File size exceeds maximum of {settings.MAX_IMAGE_SIZE} bytes",
                )
            f.write(content)

        # Validate file is actually an image (security check)
        validate_image_file(file, temp_path)

        # Calculate MD5 hash
        md5_hash = calculate_md5(temp_path)

        # Generate filename with date prefix and image_id
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        final_filename = f"{date_prefix}-{image_id}.{ext}"
        final_path = fullsize_dir / final_filename

        # Move to final location
        temp_path.rename(final_path)

        return final_path, ext, md5_hash
    except HTTPException:
        # Clean up temp file on validation error
        if temp_path.exists():
            temp_path.unlink()
        raise
    except Exception as e:
        # Clean up temp file on any error
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save image: {str(e)}",
        ) from e


async def link_tags_to_image(
    image_id: int, tag_ids: list[int], user_id: int, db: AsyncSession
) -> None:
    """Link tags to an image."""
    for tag_id in tag_ids:
        # Verify tag exists
        tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
        tag = tag_result.scalar_one_or_none()

        if not tag:
            # Skip invalid tags silently (or raise error if preferred)
            continue

        # Create tag link
        tag_link = TagLinks(
            tag_id=tag_id,
            image_id=image_id,
            user_id=user_id,
        )
        db.add(tag_link)

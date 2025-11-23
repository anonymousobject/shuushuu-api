"""
Avatar processing service for user profile images.

Handles validation, resizing, storage, and cleanup of avatar images.
"""

import hashlib
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageSequence
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models import Users
from app.services.image_processing import validate_image_file

logger = get_logger(__name__)

ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}


def validate_avatar_upload(file: UploadFile, temp_path: Path) -> None:
    """Validate uploaded avatar file.

    Checks:
    - File size is within MAX_AVATAR_SIZE limit
    - Content-Type header starts with image/
    - File extension is allowed (.jpg, .jpeg, .png, .gif)
    - File is actually a valid image (PIL verification)

    Args:
        file: The uploaded file
        temp_path: Path where file has been saved temporarily

    Raises:
        HTTPException: 400 for invalid file type, 413 for file too large
    """
    # Check file size first (avatar-specific limit)
    file_size = temp_path.stat().st_size
    if file_size > settings.MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Avatar file too large. Maximum size is {settings.MAX_AVATAR_SIZE // 1024}KB",
        )

    # Reuse shared validation for content-type, extension, and PIL verification
    validate_image_file(file, temp_path, allowed_extensions=ALLOWED_AVATAR_EXTENSIONS)


def resize_avatar(file_path: Path) -> tuple[bytes, str]:
    """Resize avatar to fit within MAX_AVATAR_DIMENSION, preserving aspect ratio.

    For animated GIFs, all frames are resized to preserve animation.

    Args:
        file_path: Path to the avatar image file

    Returns:
        Tuple of (processed image bytes, file extension without dot)
    """
    max_dim = settings.MAX_AVATAR_DIMENSION

    with Image.open(file_path) as img:
        original_format = img.format
        ext = original_format.lower() if original_format else "png"

        # Normalize extension
        if ext == "jpeg":
            ext = "jpg"

        # Handle animated GIFs
        if original_format == "GIF" and getattr(img, "is_animated", False):
            return _resize_animated_gif(img, max_dim), "gif"

        # For static images, resize if needed
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        # Convert RGBA to RGB for JPEG
        if ext == "jpg" and img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background

        # Save to bytes
        output = BytesIO()
        save_kwargs: dict[str, int | bool] = {}
        if ext == "jpg":
            save_kwargs["quality"] = 85
            save_kwargs["optimize"] = True
            img.save(output, format="JPEG", **save_kwargs)
        elif ext == "png":
            img.save(output, format="PNG", optimize=True)
        else:
            img.save(output, format=original_format or "PNG")

        return output.getvalue(), ext


def _resize_animated_gif(img: Image.Image, max_dim: int) -> bytes:
    """Resize an animated GIF while preserving all frames.

    Args:
        img: PIL Image object (animated GIF)
        max_dim: Maximum dimension for width/height

    Returns:
        Processed GIF bytes
    """
    frames = []
    durations = []

    # Process each frame
    for frame in ImageSequence.Iterator(img):
        # Get frame duration (default to 100ms if not specified)
        durations.append(frame.info.get("duration", 100))

        # Resize frame if needed
        if frame.width > max_dim or frame.height > max_dim:
            # Calculate new size maintaining aspect ratio
            ratio = min(max_dim / frame.width, max_dim / frame.height)
            new_size = (int(frame.width * ratio), int(frame.height * ratio))
            frame = frame.resize(new_size, Image.Resampling.LANCZOS)

        frames.append(frame.copy())

    # Save all frames to bytes
    output = BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:] if len(frames) > 1 else [],
        duration=durations,
        loop=img.info.get("loop", 0),
    )

    return output.getvalue()


def save_avatar(content: bytes, ext: str) -> str:
    """Save avatar content to storage with MD5-based filename.

    Args:
        content: Processed image bytes
        ext: File extension without dot (e.g., "png", "jpg", "gif")

    Returns:
        Filename (hash.ext) of saved avatar
    """
    # Calculate MD5 hash of content
    md5_hash = hashlib.md5(content).hexdigest()
    filename = f"{md5_hash}.{ext}"

    # Ensure storage directory exists
    storage_path = Path(settings.AVATAR_STORAGE_PATH)
    storage_path.mkdir(parents=True, exist_ok=True)

    # Save file
    file_path = storage_path / filename
    file_path.write_bytes(content)

    logger.info(
        "avatar_saved",
        filename=filename,
        size_bytes=len(content),
        path=str(file_path),
    )

    return filename


async def delete_avatar_if_orphaned(filename: str, db: AsyncSession) -> bool:
    """Delete avatar file from disk if no users reference it.

    Args:
        filename: Avatar filename to check
        db: Database session

    Returns:
        True if file was deleted, False otherwise
    """
    if not filename:
        return False

    # Count users with this avatar
    result = await db.execute(
        select(func.count()).select_from(Users).where(Users.avatar == filename)  # type: ignore[arg-type]
    )
    count = result.scalar() or 0

    if count == 0:
        # No users reference this avatar, safe to delete
        file_path = Path(settings.AVATAR_STORAGE_PATH) / filename
        if file_path.exists():
            file_path.unlink()
            logger.info("orphaned_avatar_deleted", filename=filename)
            return True

    return False

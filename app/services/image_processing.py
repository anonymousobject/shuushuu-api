"""
Image processing utilities for validation, dimension extraction, and thumbnail generation.
"""

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path as FilePath

from fastapi import HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy import update

from app.config import settings
from app.core.database import get_async_session
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def _update_image_variant_field(image_id: int, field: str, value: int) -> None:
    """Update medium or large field in database.

    Args:
        image_id: Image ID to update
        field: Field name ('medium' or 'large')
        value: Value to set (0 or 1)
    """
    try:
        from app.models.image import Images

        async with get_async_session() as db:
            stmt = update(Images).where(Images.image_id == image_id).values(**{field: value})
            await db.execute(stmt)
            await db.commit()
    except Exception as e:
        logger.error(
            "failed_to_update_variant_field",
            image_id=image_id,
            field=field,
            value=value,
            error=str(e),
            error_type=type(e).__name__,
        )


def _create_variant(
    source_path: FilePath,
    image_id: int,
    ext: str,
    storage_path: str,
    width: int,
    height: int,
    size_threshold: int,
    variant_type: str,
) -> bool:
    """Create an image variant (medium or large) with size validation.

    Args:
        source_path: Path to the original image file
        image_id: Database ID of the image
        ext: File extension (jpg, png, etc.)
        storage_path: Base storage directory path
        width: Original image width
        height: Original image height
        size_threshold: Maximum edge size (MEDIUM_EDGE or LARGE_EDGE)
        variant_type: Type of variant ('medium' or 'large')

    Returns:
        True if variant was created and kept, False otherwise
    """
    # Check if image exceeds threshold
    if width <= size_threshold and height <= size_threshold:
        return False

    # Bind context for this background task
    bind_context(task=f"{variant_type}_variant_generation", image_id=image_id)

    try:
        logger.info(f"{variant_type}_variant_generation_started", source_path=str(source_path))

        # Create variant directory if it doesn't exist
        variant_dir = FilePath(storage_path) / variant_type
        variant_dir.mkdir(parents=True, exist_ok=True)

        # Generate variant filename
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        variant_filename = f"{date_prefix}-{image_id}.{ext}"
        variant_path = variant_dir / variant_filename

        # Open image and create variant
        with Image.open(source_path) as img:
            original_size = img.size

            # Convert RGBA to RGB for JPEG compatibility
            if img.mode in ("RGBA", "LA") and ext.lower() in ("jpg", "jpeg"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background

            # Calculate variant size maintaining aspect ratio
            img.thumbnail(
                (size_threshold, size_threshold),
                Image.Resampling.LANCZOS,  # High-quality downsampling
            )

            # Save variant with quality setting
            save_kwargs = {}
            if ext.lower() in ("jpg", "jpeg"):
                save_kwargs["quality"] = settings.LARGE_QUALITY
                save_kwargs["optimize"] = True
            elif ext.lower() == "webp":
                save_kwargs["quality"] = settings.LARGE_QUALITY

            img.save(variant_path, **save_kwargs)

            # Check file sizes - delete variant if it's not smaller than original
            original_file_size = source_path.stat().st_size
            variant_file_size = variant_path.stat().st_size

            if variant_file_size >= original_file_size:
                # Variant is not smaller, delete it
                variant_path.unlink()
                logger.info(
                    f"{variant_type}_variant_deleted_larger_than_original",
                    variant_path=str(variant_path),
                    original_size=original_size,
                    variant_size=img.size,
                    original_file_size=original_file_size,
                    variant_file_size=variant_file_size,
                )
                # Update database to reflect that variant doesn't exist
                # Note: asyncio.run() is safe here because this function runs in FastAPI's
                # background task thread pool where no event loop exists. asyncio.run()
                # creates a new event loop in the thread for this async DB operation.
                asyncio.run(_update_image_variant_field(image_id, variant_type, 0))
                return False

            logger.info(
                f"{variant_type}_variant_generated",
                variant_path=str(variant_path),
                original_size=original_size,
                variant_size=img.size,
                original_file_size=original_file_size,
                variant_file_size=variant_file_size,
            )

        return True

    except Exception as e:
        logger.error(
            f"{variant_type}_variant_generation_failed",
            error=str(e),
            error_type=type(e).__name__,
            source_path=str(source_path),
        )
        return False


def calculate_md5(file_path: FilePath) -> str:
    """Calculate MD5 hash of a file."""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def get_image_dimensions(file_path: FilePath) -> tuple[int, int]:
    """Get image width and height."""
    with Image.open(file_path) as img:
        width, height = img.size
        return width, height


DEFAULT_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}


def validate_image_file(
    file: UploadFile,
    file_path: FilePath,
    allowed_extensions: set[str] | None = None,
) -> None:
    """Validate uploaded image file using both headers and actual file content.

    Security: Content-Type and filename are user-controlled and can be spoofed.
    This function verifies the file is actually an image by attempting to open it with PIL.

    Args:
        file: The uploaded file object
        file_path: Path where file has been saved temporarily
        allowed_extensions: Set of allowed extensions (e.g., {".jpg", ".png"}).
                          Defaults to DEFAULT_ALLOWED_EXTENSIONS if not provided.
    """
    if allowed_extensions is None:
        allowed_extensions = DEFAULT_ALLOWED_EXTENSIONS

    # Check content type (basic check, not sufficient alone)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an image",
        )

    # Check file extension (basic check, not sufficient alone)
    if file.filename:
        ext = FilePath(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File extension {ext} not allowed. Allowed: {', '.join(sorted(allowed_extensions))}",
            )

    # CRITICAL: Verify file is actually an image by attempting to open it with PIL
    # This prevents uploading malicious files with fake extensions/content-types
    try:
        with Image.open(file_path) as img:
            img.verify()  # Verify it's a valid image
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a valid image",
        ) from e


def create_thumbnail(source_path: FilePath, image_id: int, ext: str, storage_path: str) -> None:
    """Create thumbnail for uploaded image (background task).

    Generates thumbnail using settings from config:
    - MAX_THUMB_WIDTH and MAX_THUMB_HEIGHT define max dimensions
    - THUMBNAIL_QUALITY defines JPEG/WebP compression quality
    - Maintains aspect ratio
    """
    # Bind context for this background task
    bind_context(task="thumbnail_generation", image_id=image_id)

    try:
        logger.info("thumbnail_generation_started", source_path=str(source_path))

        # Create thumbs directory if it doesn't exist
        thumbs_dir = FilePath(storage_path) / "thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        # Generate thumbnail filename matching main image format
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        thumb_filename = f"{date_prefix}-{image_id}.{ext}"
        thumb_path = thumbs_dir / thumb_filename

        # Open image and create thumbnail
        with Image.open(source_path) as img:
            original_size = img.size

            # Convert RGBA to RGB for JPEG compatibility
            if img.mode in ("RGBA", "LA") and ext.lower() in ("jpg", "jpeg"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background

            # Calculate thumbnail size maintaining aspect ratio
            img.thumbnail(
                (settings.MAX_THUMB_WIDTH, settings.MAX_THUMB_HEIGHT),
                Image.Resampling.LANCZOS,  # High-quality downsampling
            )

            # Save thumbnail with quality setting
            save_kwargs = {}
            if ext.lower() in ("jpg", "jpeg"):
                save_kwargs["quality"] = settings.THUMBNAIL_QUALITY
                save_kwargs["optimize"] = True
            elif ext.lower() == "webp":
                save_kwargs["quality"] = settings.THUMBNAIL_QUALITY

            img.save(thumb_path, **save_kwargs)

            logger.info(
                "thumbnail_generated",
                thumb_path=str(thumb_path),
                original_size=original_size,
                thumbnail_size=img.size,
                file_size_bytes=thumb_path.stat().st_size,
            )

    except Exception as e:
        logger.error(
            "thumbnail_generation_failed",
            error=str(e),
            error_type=type(e).__name__,
            source_path=str(source_path),
        )


def create_medium_variant(
    source_path: FilePath, image_id: int, ext: str, storage_path: str, width: int, height: int
) -> bool:
    """Create medium-size variant if image is larger than MEDIUM_EDGE.

    Args:
        source_path: Path to the original image file
        image_id: Database ID of the image
        ext: File extension (jpg, png, etc.)
        storage_path: Base storage directory path
        width: Original image width
        height: Original image height

    Returns:
        True if medium variant was created, False otherwise
    """
    return _create_variant(
        source_path=source_path,
        image_id=image_id,
        ext=ext,
        storage_path=storage_path,
        width=width,
        height=height,
        size_threshold=settings.MEDIUM_EDGE,
        variant_type="medium",
    )


def create_large_variant(
    source_path: FilePath, image_id: int, ext: str, storage_path: str, width: int, height: int
) -> bool:
    """Create large-size variant if image is larger than LARGE_EDGE.

    Args:
        source_path: Path to the original image file
        image_id: Database ID of the image
        ext: File extension (jpg, png, etc.)
        storage_path: Base storage directory path
        width: Original image width
        height: Original image height

    Returns:
        True if large variant was created, False otherwise
    """
    return _create_variant(
        source_path=source_path,
        image_id=image_id,
        ext=ext,
        storage_path=storage_path,
        width=width,
        height=height,
        size_threshold=settings.LARGE_EDGE,
        variant_type="large",
    )

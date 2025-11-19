"""
Image processing utilities for validation, dimension extraction, and thumbnail generation.
"""

import hashlib
from datetime import datetime
from pathlib import Path as FilePath

from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.config import settings
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


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


def validate_image_file(file: UploadFile, file_path: FilePath) -> None:
    """Validate uploaded image file using both headers and actual file content.

    Security: Content-Type and filename are user-controlled and can be spoofed.
    This function verifies the file is actually an image by attempting to open it with PIL.
    """
    # Check content type (basic check, not sufficient alone)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an image",
        )

    # Check file extension (basic check, not sufficient alone)
    allowed_extensions = {".jpg", ".jpeg", ".png", ".gif"}
    if file.filename:
        ext = FilePath(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File extension {ext} not allowed. Allowed: {', '.join(allowed_extensions)}",
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

    TODO: Implement medium variant generation
    - Check if width or height > settings.MEDIUM_EDGE (1280px)
    - Resize maintaining aspect ratio
    - Save as YYYY-MM-DD-{image_id}-medium.{ext}
    - Use settings.LARGE_QUALITY for compression

    Returns:
        True if medium variant was created, False otherwise
    """
    # Placeholder - always return False (not created)
    # When implemented:
    # if width > settings.MEDIUM_EDGE or height > settings.MEDIUM_EDGE:
    #     # Create resized version
    #     # Save to fullsize/YYYY-MM-DD-{image_id}-medium.{ext}
    #     return True
    return False


def create_large_variant(
    source_path: FilePath, image_id: int, ext: str, storage_path: str, width: int, height: int
) -> bool:
    """Create large-size variant if image is larger than LARGE_EDGE.

    TODO: Implement large variant generation
    - Check if width or height > settings.LARGE_EDGE (2048px)
    - Resize maintaining aspect ratio
    - Save as YYYY-MM-DD-{image_id}-large.{ext}
    - Use settings.LARGE_QUALITY for compression

    Returns:
        True if large variant was created, False otherwise
    """
    # Placeholder - always return False (not created)
    # When implemented:
    # if width > settings.LARGE_EDGE or height > settings.LARGE_EDGE:
    #     # Create resized version
    #     # Save to fullsize/YYYY-MM-DD-{image_id}-large.{ext}
    #     return True
    return False

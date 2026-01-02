"""
Media file serving endpoints with permission checks.

Routes:
- GET /images/{filename} - Serve fullsize image with permission check
- GET /thumbs/{filename} - Serve thumbnail with permission check

These endpoints return X-Accel-Redirect headers for nginx to serve the actual files.
Authentication is cookie-based (access_token HTTPOnly cookie).

Note: Future enhancement could support ?token=xxx query param for non-browser clients.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.models.image import Images
from app.models.user import Users
from app.services.image_visibility import can_view_image_file

router = APIRouter()


def parse_image_id_from_filename(filename: str) -> int | None:
    """
    Extract image_id from filename like '2026-01-02-1112196.png'.

    Args:
        filename: The filename to parse (e.g., "2026-01-02-1112196.png")

    Returns:
        The image_id as integer, or None if parsing fails
    """
    if not filename or "." not in filename:
        return None

    try:
        # Remove extension: "2026-01-02-1112196.png" -> "2026-01-02-1112196"
        name_without_ext = filename.rsplit(".", 1)[0]
        # Get last segment after dash: "2026-01-02-1112196" -> "1112196"
        image_id_str = name_without_ext.rsplit("-", 1)[-1]
        return int(image_id_str)
    except (ValueError, IndexError):
        return None


def get_extension_from_filename(filename: str) -> str:
    """
    Extract file extension from filename.

    Args:
        filename: The filename to parse

    Returns:
        The extension (without dot), or empty string if no extension
    """
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1]


@router.get("/images/{filename}")
async def serve_fullsize_image(
    filename: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_optional_current_user)],
) -> Response:
    """
    Serve fullsize image with permission check.
    Returns X-Accel-Redirect header for nginx to serve the actual file.
    Authentication: Cookie-based (access_token).
    """
    return await _serve_image(filename, "fullsize", db, current_user)


@router.get("/thumbs/{filename}")
async def serve_thumbnail(
    filename: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_optional_current_user)],
) -> Response:
    """
    Serve thumbnail with permission check.
    Returns X-Accel-Redirect header for nginx to serve the actual file.
    Note: Thumbnails are always JPEG format.
    """
    return await _serve_image(filename, "thumbs", db, current_user)


async def _serve_image(
    filename: str,
    image_type: Literal["fullsize", "thumbs"],
    db: AsyncSession,
    current_user: Users | None,
) -> Response:
    """Internal handler for serving images."""
    image_id = parse_image_id_from_filename(filename)
    if image_id is None:
        raise HTTPException(status_code=404)

    image = await db.get(Images, image_id)
    if image is None:
        raise HTTPException(status_code=404)

    if not await can_view_image_file(image, current_user, db):
        raise HTTPException(status_code=404)

    # Use database extension for fullsize, always jpeg for thumbnails
    if image_type == "thumbs":
        ext = "jpeg"
    else:
        ext = image.ext

    internal_path = f"/internal/{image_type}/{image.md5_hash}.{ext}"
    return Response(status_code=200, headers={"X-Accel-Redirect": internal_path})

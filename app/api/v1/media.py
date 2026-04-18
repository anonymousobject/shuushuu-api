"""
Media file serving endpoints with permission checks.

Routes:
- GET /images/{filename} - Serve fullsize image with permission check
- GET /thumbs/{filename} - Serve thumbnail with permission check
- GET /medium/{filename} - Serve medium variant (1280px) with permission check
- GET /large/{filename} - Serve large variant (2048px) with permission check

These endpoints serve images via X-Accel-Redirect (local FS) or HTTP 302 redirect (R2 CDN / presigned URL).
Authentication is cookie-based (access_token HTTPOnly cookie).

Note: Future enhancement could support ?token=xxx query param for non-browser clients.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.r2_client import get_r2_storage
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.models.user import Users
from app.services.image_visibility import can_view_image_file

logger = get_logger(__name__)

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
    Note: Thumbnails are always WebP format.
    """
    return await _serve_image(filename, "thumbs", db, current_user)


@router.get("/medium/{filename}")
async def serve_medium_image(
    filename: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_optional_current_user)],
) -> Response:
    """
    Serve medium variant (1280px edge) with permission check.
    Returns 404 if medium variant doesn't exist for this image.
    """
    return await _serve_image(filename, "medium", db, current_user)


@router.get("/large/{filename}")
async def serve_large_image(
    filename: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_optional_current_user)],
) -> Response:
    """
    Serve large variant (2048px edge) with permission check.
    Returns 404 if large variant doesn't exist for this image.
    """
    return await _serve_image(filename, "large", db, current_user)


async def _serve_image(
    filename: str,
    image_type: Literal["fullsize", "thumbs", "medium", "large"],
    db: AsyncSession,
    current_user: Users | None,
) -> Response:
    """Internal handler for serving images.

    Routing:
      - Permission check first (prevents leaking protected-image existence).
      - If R2 enabled and r2_location=PUBLIC → 302 to CDN URL.
      - If R2 enabled and r2_location=PRIVATE → 302 to presigned URL.
      - Otherwise → X-Accel-Redirect to local /internal/ path (legacy fallback).
    """
    image_id = parse_image_id_from_filename(filename)
    if image_id is None:
        raise HTTPException(status_code=404)

    image = await db.get(Images, image_id)
    if image is None:
        raise HTTPException(status_code=404)

    # Permission check — prevents leaking info about protected images
    if not await can_view_image_file(image, current_user, db):
        raise HTTPException(status_code=404)

    # Variant checks (unchanged)
    if image_type in ("medium", "large"):
        variant_status = image.medium if image_type == "medium" else image.large
        if variant_status == VariantStatus.NONE:
            raise HTTPException(status_code=404)
        if variant_status == VariantStatus.PENDING:
            # TODO(r2): Pending variant falls back to local /internal/fullsize/ path even
            # when the fullsize is in R2. Acceptable during migration (files exist locally
            # until r2_sync backfills them), but must be revisited once local FS is retired.
            fullsize_path = f"/internal/fullsize/{image.filename}.{image.ext}"
            return Response(
                status_code=200,
                headers={"X-Accel-Redirect": fullsize_path, "Cache-Control": "no-store"},
            )

    ext = "webp" if image_type == "thumbs" else image.ext
    key = f"{image_type}/{image.filename}.{ext}"

    # R2 branches (only active when R2_ENABLED)
    if settings.R2_ENABLED and image.r2_location == R2Location.PUBLIC:
        cdn_url = f"{settings.R2_PUBLIC_CDN_URL}/{key}"
        return Response(
            status_code=302,
            headers={"Location": cdn_url, "Cache-Control": "no-store"},
        )

    if settings.R2_ENABLED and image.r2_location == R2Location.PRIVATE:
        r2 = get_r2_storage()
        presigned = await r2.generate_presigned_url(
            bucket=settings.R2_PRIVATE_BUCKET,
            key=key,
            ttl=settings.R2_PRESIGN_TTL_SECONDS,
        )
        logger.debug("r2_presigned_url_issued", image_id=image_id, variant=image_type)
        return Response(
            status_code=302,
            headers={"Location": presigned, "Cache-Control": "no-store"},
        )

    # Local FS fallback (r2_location=NONE, or R2 disabled)
    return Response(
        status_code=200,
        headers={"X-Accel-Redirect": f"/internal/{image_type}/{image.filename}.{ext}"},
    )

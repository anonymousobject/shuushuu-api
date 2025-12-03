"""Image processing background jobs for arq worker."""

from pathlib import Path as FilePath

from arq import Retry

from app.config import settings
from app.core.logging import bind_context, get_logger

logger = get_logger(__name__)


async def create_thumbnail_job(
    ctx: dict,
    image_id: int,
    source_path: str,
    ext: str,
    storage_path: str,
) -> dict[str, bool | str]:
    """
    Create thumbnail for uploaded image.

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        source_path: Path to original image file
        ext: File extension (jpg, png, etc.)
        storage_path: Base storage directory

    Returns:
        dict with success status and thumbnail_path

    Raises:
        Retry: If thumbnail generation fails (will retry up to max_tries)
    """
    bind_context(task="thumbnail_generation", image_id=image_id)

    try:
        # Import here to avoid loading PIL at module level
        from app.services.image_processing import create_thumbnail

        # Call existing sync function (runs in thread pool)
        create_thumbnail(
            source_path=FilePath(source_path),
            image_id=image_id,
            ext=ext,
            storage_path=storage_path,
        )

        thumb_path = f"{storage_path}/thumbs/{image_id}.{ext}"
        logger.info("thumbnail_job_completed", image_id=image_id, path=thumb_path)

        return {"success": True, "thumbnail_path": thumb_path}

    except Exception as e:
        logger.error(
            "thumbnail_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Retry with exponential backoff (arq default: 0s, 5s, 15s, 30s, etc.)
        raise Retry(defer=ctx["job_try"] * 5) from e


async def create_variant_job(
    ctx: dict,
    image_id: int,
    source_path: str,
    ext: str,
    storage_path: str,
    width: int,
    height: int,
    variant_type: str,
) -> dict[str, bool]:
    """
    Create image variant (medium or large).

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        source_path: Path to original image
        ext: File extension
        storage_path: Base storage directory
        width: Original image width
        height: Original image height
        variant_type: 'medium' or 'large'

    Returns:
        dict with success status

    Raises:
        Retry: If variant generation fails
    """
    bind_context(task=f"{variant_type}_variant_generation", image_id=image_id)

    try:
        from app.services.image_processing import _create_variant

        result = _create_variant(
            source_path=FilePath(source_path),
            image_id=image_id,
            ext=ext,
            storage_path=storage_path,
            width=width,
            height=height,
            size_threshold=settings.MEDIUM_EDGE
            if variant_type == "medium"
            else settings.LARGE_EDGE,
            variant_type=variant_type,
        )

        logger.info(f"{variant_type}_variant_job_completed", image_id=image_id, created=result)

        return {"success": True, "created": result}

    except Exception as e:
        logger.error(
            f"{variant_type}_variant_job_failed",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise Retry(defer=ctx["job_try"] * 5) from e


async def add_to_iqdb_job(
    ctx: dict,
    image_id: int,
    thumb_path: str,
) -> dict[str, bool]:
    """
    Add image thumbnail to IQDB index.

    NOTE: This should be enqueued AFTER thumbnail_job completes.
    No more polling/sleep hacks!

    Args:
        ctx: ARQ context dict
        image_id: Database image ID
        thumb_path: Path to thumbnail file

    Returns:
        dict with success status

    Raises:
        Retry: If IQDB is unavailable
    """
    bind_context(task="iqdb_indexing", image_id=image_id)

    try:
        import httpx

        thumb_file = FilePath(thumb_path)

        # Verify thumbnail exists (should always exist since we depend on thumbnail job)
        if not thumb_file.exists():
            logger.error("iqdb_job_thumbnail_missing", image_id=image_id, path=thumb_path)
            return {"success": False, "error": "thumbnail_not_found"}

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        with open(thumb_file, "rb") as f:
            files = {"file": (thumb_file.name, f, "image/jpeg")}

            # Use sync httpx client (worker runs in thread pool)
            with httpx.Client(timeout=10.0) as client:
                response = client.post(iqdb_url, files=files)

        if response.status_code in (200, 201):
            logger.info("iqdb_job_completed", image_id=image_id)
            return {"success": True}
        else:
            logger.warning(
                "iqdb_job_failed_status",
                image_id=image_id,
                status_code=response.status_code,
            )
            # Retry if IQDB returned error
            raise Retry(defer=ctx["job_try"] * 10)

    except httpx.RequestError as e:
        logger.error("iqdb_job_request_failed", image_id=image_id, error=str(e))
        # Retry on network errors
        raise Retry(defer=ctx["job_try"] * 10) from e

    except Exception as e:
        logger.error(
            "iqdb_job_unexpected_error",
            image_id=image_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return {"success": False, "error": str(e)}

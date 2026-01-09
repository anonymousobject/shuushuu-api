"""
IQDB (Image Quality Database) integration for similarity search and image indexing.
"""

from pathlib import Path as FilePath

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def check_iqdb_similarity(
    file_path: FilePath, db: AsyncSession, threshold: float | None = None
) -> list[dict[str, int | float]]:
    """Query IQDB for similar images using REST API.

    Makes HTTP POST request to IQDB query endpoint with image file.
    Returns list of similar images that meet the similarity threshold.

    Args:
        file_path: Path to image file (typically thumbnail)
        db: Database session for querying image details
        threshold: Minimum similarity score (0-100), defaults to settings.IQDB_SIMILARITY_THRESHOLD

    Returns:
        List of dicts with {image_id, score} for similar images
        Empty list if no similar images or IQDB unavailable

    Example IQDB response:
        [
            {"image_id": 1111806, "score": 95.5},
            {"image_id": 1111807, "score": 87.3}
        ]
    """
    if threshold is None:
        threshold = settings.IQDB_SIMILARITY_THRESHOLD

    try:
        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/query"

        # Read image file
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "image/jpeg")}

            # Query IQDB with 5 second timeout
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(iqdb_url, files=files)

        # Check if request succeeded
        if response.status_code != 200:
            # IQDB unavailable, return empty list (don't block upload)
            return []

        # Parse response JSON
        results = response.json()

        # Filter by threshold and return
        # Note: IQDB uses "post_id" as the key for image IDs
        similar_images = [
            {"image_id": result["post_id"], "score": result["score"]}
            for result in results
            if result.get("score", 0) >= threshold
        ]

        return similar_images

    except (httpx.RequestError, httpx.TimeoutException, ValueError, KeyError):
        # IQDB unavailable or response parse error
        # Don't block upload - return empty list
        return []
    except FileNotFoundError:
        # Image file doesn't exist yet
        return []


def add_to_iqdb(image_id: int, thumb_path: FilePath) -> None:
    """Add image to IQDB index for future similarity searches using REST API.

    Called by add_to_iqdb_job in app/tasks/image_jobs.py (ARQ task).
    Makes HTTP POST request to IQDB images endpoint to index the thumbnail.

    Args:
        image_id: Database image ID to associate with IQDB entry
        thumb_path: Path to thumbnail file to index

    Example API call:
        POST http://localhost:5588/images/{image_id}
        Content-Type: multipart/form-data
        file: <thumbnail bytes>

    Note: Errors are silently ignored as this is non-critical.
    """
    try:
        # Thumbnail should exist (job ordering handled by arq defer)
        if not thumb_path.exists():
            # Log warning but don't crash
            logger.warning("iqdb_thumbnail_missing", image_id=image_id, path=str(thumb_path))
            return

        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        # Read thumbnail file
        with open(thumb_path, "rb") as f:
            files = {"file": (thumb_path.name, f, "image/jpeg")}

            # Use sync httpx client since this runs in background thread
            with httpx.Client(timeout=10.0) as client:
                response = client.post(iqdb_url, files=files)

        # Log success/failure if needed
        # For now, silently ignore errors (non-critical operation)
        if response.status_code not in (200, 201):
            # Could log warning here
            pass

    except Exception:
        # Silently fail - IQDB insertion is non-critical
        # Could log error here if logging is configured
        pass


def remove_from_iqdb(image_id: int) -> bool:
    """Remove image from IQDB index using REST API.

    Makes HTTP DELETE request to IQDB images endpoint.

    Args:
        image_id: Database image ID to remove from IQDB

    Returns:
        True if successfully removed (or didn't exist), False on error

    Example API call:
        DELETE http://localhost:5588/images/{image_id}
    """
    try:
        iqdb_url = f"http://{settings.IQDB_HOST}:{settings.IQDB_PORT}/images/{image_id}"

        with httpx.Client(timeout=10.0) as client:
            response = client.delete(iqdb_url)

        # 200/204 = deleted, 404 = didn't exist (both are success)
        if response.status_code in (200, 204, 404):
            logger.info("iqdb_image_removed", image_id=image_id)
            return True

        logger.warning(
            "iqdb_remove_failed",
            image_id=image_id,
            status_code=response.status_code,
        )
        return False

    except Exception as e:
        logger.error("iqdb_remove_error", image_id=image_id, error=str(e))
        return False

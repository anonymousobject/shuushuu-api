"""
Media file serving endpoints with permission checks.

Routes:
- GET /images/{filename} - Serve fullsize image with permission check
- GET /thumbs/{filename} - Serve thumbnail with permission check

These endpoints return X-Accel-Redirect headers for nginx to serve the actual files.
Authentication is cookie-based (access_token HTTPOnly cookie).

Note: Future enhancement could support ?token=xxx query param for non-browser clients.
"""

from fastapi import APIRouter

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

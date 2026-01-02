# Image Visibility Protection Design

**Date:** 2026-01-02
**Status:** Approved
**Problem:** Users can view disabled images (status 0, -1, -2, -3, -4) by accessing file URLs directly, even when those images are under review or marked inappropriate.

## Goal

Protect image files from unauthorized viewing while keeping API metadata accessible. Only admins, moderators, and the image uploader should be able to view protected image files.

## Visibility Rules

| User Type | Can View |
|-----------|----------|
| Anonymous / Regular users | `status IN (-1, 1, 2)` — REPOST, ACTIVE, SPOILER |
| Image uploader | Own images regardless of status |
| Users with `IMAGE_EDIT` or `REVIEW_VIEW` permission | All images |

**Status reference:**
- `-4` REVIEW — Under moderator review (protected)
- `-3` LOW_QUALITY — Marked low quality (protected)
- `-2` INAPPROPRIATE — Flagged inappropriate (protected)
- `-1` REPOST — Duplicate/repost (public)
- `0` OTHER — Uncategorized/flagged (protected)
- `1` ACTIVE — Normal public image (public)
- `2` SPOILER — Visible but marked spoiler (public)

## Architecture

### Request Flow

```
Browser → nginx → FastAPI → (permission check) → X-Accel-Redirect → nginx → file
```

### URL Patterns

Public-facing URLs (unchanged from legacy):
- Fullsize: `/images/{date}-{image_id}.{ext}` (e.g., `/images/2026-01-02-1112196.png`)
- Thumbnail: `/thumbs/{date}-{image_id}.{ext}` (e.g., `/thumbs/2026-01-02-1112196.png`)

Internal nginx locations (not directly accessible):
- `/internal/fullsize/{hash}.{ext}`
- `/internal/thumbs/{hash}.{ext}`

### Authentication

Cookie-based using existing `access_token` HTTPOnly cookie.

```python
# Future enhancement: Support query param for non-browser clients
# token = request.cookies.get("access_token") or request.query_params.get("token")
```

### Error Handling

Unauthorized requests return **404 Not Found** (not 403) to avoid revealing existence of hidden content.

### Caching

Normal nginx caching is allowed. The goal is preventing new unauthorized access, not purging existing browser caches.

## Implementation

### New Files

**`app/api/v1/media.py`** — File serving endpoints

```python
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_current_user_optional
from app.models import Users, Images
from app.services.image import can_view_image_file
from app.config import settings

router = APIRouter()

@router.get("/images/{filename}")
async def serve_fullsize_image(
    filename: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_current_user_optional)],
) -> Response:
    """
    Serve fullsize image with permission check.

    Authentication: Cookie-based (access_token).
    # TODO: Support ?token=xxx query param for non-browser clients
    """
    return await _serve_image(filename, "fullsize", db, current_user)


@router.get("/thumbs/{filename}")
async def serve_thumbnail(
    filename: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Users | None, Depends(get_current_user_optional)],
) -> Response:
    """
    Serve thumbnail with permission check.

    Authentication: Cookie-based (access_token).
    # TODO: Support ?token=xxx query param for non-browser clients
    """
    return await _serve_image(filename, "thumbs", db, current_user)


async def _serve_image(
    filename: str,
    image_type: str,  # "fullsize" or "thumbs"
    db: AsyncSession,
    current_user: Users | None,
) -> Response:
    # Parse image_id from filename (e.g., "2026-01-02-1112196.png" -> 1112196)
    image_id = _parse_image_id(filename)
    if image_id is None:
        raise HTTPException(status_code=404)

    # Fetch image
    image = await db.get(Images, image_id)
    if image is None:
        raise HTTPException(status_code=404)

    # Permission check
    if not await can_view_image_file(image, current_user, db):
        raise HTTPException(status_code=404)  # 404, not 403

    # Return X-Accel-Redirect to internal nginx location
    internal_path = f"/internal/{image_type}/{image.file_hash}.{_get_extension(filename)}"
    return Response(
        status_code=200,
        headers={"X-Accel-Redirect": internal_path},
    )


def _parse_image_id(filename: str) -> int | None:
    """Extract image_id from filename like '2026-01-02-1112196.png'"""
    try:
        name = filename.rsplit(".", 1)[0]  # Remove extension
        image_id_str = name.rsplit("-", 1)[-1]  # Get last segment after dash
        return int(image_id_str)
    except (ValueError, IndexError):
        return None


def _get_extension(filename: str) -> str:
    """Extract extension from filename"""
    return filename.rsplit(".", 1)[-1] if "." in filename else ""
```

### Modified Files

**`app/services/image.py`** — Add visibility helper

```python
from app.core.permissions import has_permission, Permission

# Public statuses that anyone can view
PUBLIC_IMAGE_STATUSES = (-1, 1, 2)  # REPOST, ACTIVE, SPOILER


async def can_view_image_file(
    image: Images,
    user: Users | None,
    db: AsyncSession,
) -> bool:
    """
    Check if a user can view an image file.

    Visibility rules:
    - Public statuses (-1, 1, 2): Anyone can view
    - Owner: Can view their own images regardless of status
    - Moderators: Users with IMAGE_EDIT or REVIEW_VIEW can view all
    """
    # Public statuses are visible to all
    if image.status in PUBLIC_IMAGE_STATUSES:
        return True

    # Anonymous users can only see public statuses
    if user is None:
        return False

    # Owner can view their own images
    if image.user_id == user.user_id:
        return True

    # Moderators can view all
    if await has_permission(user.user_id, Permission.IMAGE_EDIT, db):
        return True
    if await has_permission(user.user_id, Permission.REVIEW_VIEW, db):
        return True

    return False
```

**`app/core/auth.py`** — Ensure `get_current_user_optional` exists

This dependency should return `None` for anonymous users instead of raising 401.

### nginx Configuration

```nginx
# Proxy image/thumb requests to FastAPI for permission check
location ~ ^/images/\d{4}-\d{2}-\d{2}-\d+\.(png|jpg|jpeg|gif|webp)$ {
    proxy_pass http://fastapi:8000;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
}

location ~ ^/thumbs/\d{4}-\d{2}-\d{2}-\d+\.(png|jpg|jpeg|gif|webp)$ {
    proxy_pass http://fastapi:8000;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
}

# Internal locations - only accessible via X-Accel-Redirect
# STORAGE_PATH should be substituted from environment variable
location /internal/fullsize/ {
    internal;
    alias /path/to/storage/fullsize/;  # Use STORAGE_PATH from env
}

location /internal/thumbs/ {
    internal;
    alias /path/to/storage/thumbs/;  # Use STORAGE_PATH from env
}
```

## Scope Clarification

**Protected by this design:**
- Image file access (`/images/*.png`, `/thumbs/*.png`)

**NOT changed by this design:**
- API metadata endpoints (`/api/v1/images`, `/api/v1/images/{id}`) — These continue to return metadata for all images regardless of status
- The frontend is responsible for checking image status and displaying placeholders for disabled images

## Future Enhancements

1. **Query param authentication** — Support `?token=xxx` for non-browser clients (API apps, tools)
2. **Signed URLs** — Time-limited URLs for specific use cases
3. **Rate limiting** — Prevent enumeration attacks on image IDs

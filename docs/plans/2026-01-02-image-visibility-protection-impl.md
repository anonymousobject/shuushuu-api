# Image Visibility Protection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Protect image files from unauthorized viewing using X-Accel-Redirect, while allowing metadata API access.

**Architecture:** New `/images/` and `/thumbs/` endpoints at root level (not under /api/v1) that check visibility permissions and return X-Accel-Redirect headers for nginx to serve files. The existing `get_optional_current_user` auth dependency is reused.

**Tech Stack:** FastAPI, SQLAlchemy async, existing permission system (`has_any_permission`)

**Design Doc:** `docs/plans/2026-01-02-image-visibility-protection-design.md`

---

## Task 1: Create Image Visibility Service - Unit Tests

**Files:**
- Create: `tests/unit/test_image_visibility.py`
- Create: `app/services/image_visibility.py`

**Step 1: Write failing tests for visibility logic**

```python
"""Tests for image visibility service."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch

from app.config import ImageStatus
from app.models.image import Images
from app.models.user import Users
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES, can_view_image_file


class TestPublicImageStatuses:
    """Test the PUBLIC_IMAGE_STATUSES constant."""

    def test_public_statuses_include_repost(self):
        """REPOST (-1) should be publicly visible."""
        assert ImageStatus.REPOST in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_include_active(self):
        """ACTIVE (1) should be publicly visible."""
        assert ImageStatus.ACTIVE in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_include_spoiler(self):
        """SPOILER (2) should be publicly visible."""
        assert ImageStatus.SPOILER in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_review(self):
        """REVIEW (-4) should NOT be publicly visible."""
        assert ImageStatus.REVIEW not in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_inappropriate(self):
        """INAPPROPRIATE (-2) should NOT be publicly visible."""
        assert ImageStatus.INAPPROPRIATE not in PUBLIC_IMAGE_STATUSES

    def test_public_statuses_exclude_other(self):
        """OTHER (0) should NOT be publicly visible."""
        assert ImageStatus.OTHER not in PUBLIC_IMAGE_STATUSES


class TestCanViewImageFile:
    """Tests for can_view_image_file function."""

    @pytest.fixture
    def mock_image(self):
        """Create a mock image with configurable status and user_id."""
        image = Images(
            image_id=1,
            filename="test",
            ext="png",
            md5_hash="abc123",
            filesize=1000,
            width=100,
            height=100,
            user_id=10,
            status=ImageStatus.ACTIVE,
        )
        return image

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = Users(
            user_id=20,
            username="testuser",
            password="hash",
            password_type="bcrypt",
            salt="1234567890123456",
            email="test@example.com",
        )
        return user

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock(spec=AsyncSession)

    # === Public status tests ===

    async def test_active_image_visible_to_anonymous(self, mock_image, mock_db):
        """ACTIVE images are visible to anonymous users."""
        mock_image.status = ImageStatus.ACTIVE
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    async def test_spoiler_image_visible_to_anonymous(self, mock_image, mock_db):
        """SPOILER images are visible to anonymous users."""
        mock_image.status = ImageStatus.SPOILER
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    async def test_repost_image_visible_to_anonymous(self, mock_image, mock_db):
        """REPOST images are visible to anonymous users."""
        mock_image.status = ImageStatus.REPOST
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is True

    # === Protected status tests - anonymous ===

    async def test_review_image_hidden_from_anonymous(self, mock_image, mock_db):
        """REVIEW images are hidden from anonymous users."""
        mock_image.status = ImageStatus.REVIEW
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    async def test_inappropriate_image_hidden_from_anonymous(self, mock_image, mock_db):
        """INAPPROPRIATE images are hidden from anonymous users."""
        mock_image.status = ImageStatus.INAPPROPRIATE
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    async def test_other_image_hidden_from_anonymous(self, mock_image, mock_db):
        """OTHER images are hidden from anonymous users."""
        mock_image.status = ImageStatus.OTHER
        result = await can_view_image_file(mock_image, None, mock_db)
        assert result is False

    # === Protected status tests - non-owner regular user ===

    async def test_review_image_hidden_from_non_owner(self, mock_image, mock_user, mock_db):
        """REVIEW images are hidden from non-owner regular users."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Different from mock_user.user_id
        with patch("app.services.image_visibility.has_any_permission", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = False
            result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is False

    # === Owner visibility tests ===

    async def test_owner_can_view_review_image(self, mock_image, mock_user, mock_db):
        """Owners can view their own REVIEW images."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = mock_user.user_id
        result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    async def test_owner_can_view_inappropriate_image(self, mock_image, mock_user, mock_db):
        """Owners can view their own INAPPROPRIATE images."""
        mock_image.status = ImageStatus.INAPPROPRIATE
        mock_image.user_id = mock_user.user_id
        result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    # === Moderator visibility tests ===

    async def test_moderator_with_image_edit_can_view_review(self, mock_image, mock_user, mock_db):
        """Users with IMAGE_EDIT permission can view REVIEW images."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Not the owner
        with patch("app.services.image_visibility.has_any_permission", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = True
            result = await can_view_image_file(mock_image, mock_user, mock_db)
        assert result is True

    async def test_permission_check_uses_correct_permissions(self, mock_image, mock_user, mock_db):
        """Verify that IMAGE_EDIT and REVIEW_VIEW permissions are checked."""
        mock_image.status = ImageStatus.REVIEW
        mock_image.user_id = 999  # Not the owner
        with patch("app.services.image_visibility.has_any_permission", new_callable=AsyncMock) as mock_perm:
            mock_perm.return_value = False
            await can_view_image_file(mock_image, mock_user, mock_db)
            # Verify the correct permissions were checked
            from app.core.permissions import Permission
            mock_perm.assert_called_once()
            call_args = mock_perm.call_args
            assert call_args[0][1] == mock_user.user_id
            permissions_arg = call_args[0][2]
            assert Permission.IMAGE_EDIT in permissions_arg
            assert Permission.REVIEW_VIEW in permissions_arg
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_image_visibility.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'app.services.image_visibility'`

**Step 3: Create minimal implementation**

```python
"""
Image visibility service.

Determines whether a user can view an image file based on:
- Image status (public vs protected)
- User ownership (owners can view their own images)
- User permissions (moderators can view all images)

Note: This controls FILE access only. API metadata endpoints remain unrestricted.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.permissions import Permission, has_any_permission
from app.models.image import Images
from app.models.user import Users


# Public statuses that anyone can view
# REPOST (-1), ACTIVE (1), SPOILER (2)
PUBLIC_IMAGE_STATUSES: frozenset[int] = frozenset({
    ImageStatus.REPOST,
    ImageStatus.ACTIVE,
    ImageStatus.SPOILER,
})


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

    Args:
        image: The image to check visibility for
        user: The requesting user (None for anonymous)
        db: Database session for permission lookups

    Returns:
        True if user can view the image file, False otherwise

    Note:
        Future enhancement: Support ?token=xxx query param for non-browser clients.
        See design doc for details.
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

    # Moderators can view all - check for IMAGE_EDIT or REVIEW_VIEW
    return await has_any_permission(
        db,
        user.user_id,
        [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW],
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_image_visibility.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add tests/unit/test_image_visibility.py app/services/image_visibility.py
git commit -m "feat: add image visibility service with TDD tests"
```

---

## Task 2: Create Media Router - Filename Parsing Tests

**Files:**
- Create: `tests/api/v1/test_media.py`
- Create: `app/api/v1/media.py`

**Step 1: Write failing tests for filename parsing**

```python
"""Tests for media file serving endpoints."""

import pytest

from app.api.v1.media import parse_image_id_from_filename, get_extension_from_filename


class TestFilenameParsing:
    """Tests for filename parsing utilities."""

    def test_parse_valid_filename(self):
        """Parse image_id from valid filename like '2026-01-02-1112196.png'."""
        result = parse_image_id_from_filename("2026-01-02-1112196.png")
        assert result == 1112196

    def test_parse_filename_with_jpeg(self):
        """Parse image_id from jpeg filename."""
        result = parse_image_id_from_filename("2025-12-31-999.jpeg")
        assert result == 999

    def test_parse_invalid_filename_no_id(self):
        """Return None for filename without image_id."""
        result = parse_image_id_from_filename("invalid.png")
        assert result is None

    def test_parse_invalid_filename_no_extension(self):
        """Return None for filename without extension."""
        result = parse_image_id_from_filename("2026-01-02-1112196")
        assert result is None

    def test_parse_invalid_filename_non_numeric_id(self):
        """Return None for filename with non-numeric id."""
        result = parse_image_id_from_filename("2026-01-02-abc.png")
        assert result is None

    def test_parse_empty_filename(self):
        """Return None for empty filename."""
        result = parse_image_id_from_filename("")
        assert result is None

    def test_get_extension_png(self):
        """Get extension from png filename."""
        result = get_extension_from_filename("2026-01-02-123.png")
        assert result == "png"

    def test_get_extension_jpeg(self):
        """Get extension from jpeg filename."""
        result = get_extension_from_filename("test.jpeg")
        assert result == "jpeg"

    def test_get_extension_none(self):
        """Return empty string for filename without extension."""
        result = get_extension_from_filename("noextension")
        assert result == ""
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_media.py::TestFilenameParsing -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'app.api.v1.media'`

**Step 3: Create minimal implementation (parsing only)**

```python
"""
Media file serving endpoints with permission checks.

Routes:
- GET /images/{filename} - Serve fullsize image with permission check
- GET /thumbs/{filename} - Serve thumbnail with permission check

These endpoints return X-Accel-Redirect headers for nginx to serve the actual files.
Authentication is cookie-based (access_token HTTPOnly cookie).

Note: Future enhancement could support ?token=xxx query param for non-browser clients.
"""

from typing import Annotated

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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_media.py::TestFilenameParsing -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/api/v1/test_media.py app/api/v1/media.py
git commit -m "feat: add media router with filename parsing utilities"
```

---

## Task 3: Create Media Router - Endpoint Tests

**Files:**
- Modify: `tests/api/v1/test_media.py`
- Modify: `app/api/v1/media.py`
- Modify: `app/main.py`

**Step 1: Write failing tests for endpoints**

Add to `tests/api/v1/test_media.py`:

```python
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, AsyncMock

from app.config import ImageStatus
from app.core.security import create_access_token
from app.core.permissions import Permission
from app.models.image import Images
from app.models.user import Users
from app.models.permissions import Perms, UserPerms


class TestServeImageEndpoint:
    """Tests for GET /images/{filename} endpoint."""

    @pytest.fixture
    async def public_image(self, db_session: AsyncSession):
        """Create a public (ACTIVE) image."""
        image = Images(
            image_id=100,
            filename="2026-01-02-100",
            ext="png",
            md5_hash="abc123public",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.fixture
    async def protected_image(self, db_session: AsyncSession):
        """Create a protected (REVIEW) image."""
        image = Images(
            image_id=200,
            filename="2026-01-02-200",
            ext="png",
            md5_hash="abc123protected",
            filesize=1000,
            width=100,
            height=100,
            user_id=1,  # Owned by testuser (user_id=1)
            status=ImageStatus.REVIEW,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_invalid_filename_returns_404(self, client: AsyncClient):
        """Invalid filename format returns 404."""
        response = await client.get("/images/invalid.png")
        assert response.status_code == 404

    async def test_nonexistent_image_returns_404(self, client: AsyncClient):
        """Non-existent image_id returns 404."""
        response = await client.get("/images/2026-01-02-99999999.png")
        assert response.status_code == 404

    async def test_public_image_anonymous_returns_xaccel(self, client: AsyncClient, public_image: Images):
        """Public image returns X-Accel-Redirect for anonymous user."""
        response = await client.get(f"/images/2026-01-02-{public_image.image_id}.png")
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers
        assert f"/internal/fullsize/{public_image.md5_hash}.png" in response.headers["X-Accel-Redirect"]

    async def test_protected_image_anonymous_returns_404(self, client: AsyncClient, protected_image: Images):
        """Protected image returns 404 for anonymous user (not 403 to hide existence)."""
        response = await client.get(f"/images/2026-01-02-{protected_image.image_id}.png")
        assert response.status_code == 404

    async def test_protected_image_owner_returns_xaccel(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns X-Accel-Redirect for owner."""
        # Get the owner (user_id=1, created by conftest)
        owner = await db_session.get(Users, 1)
        token = create_access_token(owner.user_id)

        response = await client.get(
            f"/images/2026-01-02-{protected_image.image_id}.png",
            cookies={"access_token": token},
        )
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers

    async def test_protected_image_non_owner_returns_404(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns 404 for non-owner regular user."""
        # Get user_id=2 (not the owner)
        non_owner = await db_session.get(Users, 2)
        token = create_access_token(non_owner.user_id)

        response = await client.get(
            f"/images/2026-01-02-{protected_image.image_id}.png",
            cookies={"access_token": token},
        )
        assert response.status_code == 404

    async def test_protected_image_moderator_returns_xaccel(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns X-Accel-Redirect for moderator with IMAGE_EDIT."""
        # Get user_id=2 and give them IMAGE_EDIT permission
        moderator = await db_session.get(Users, 2)

        # Create the permission in database
        perm = Perms(perm_id=1, title=Permission.IMAGE_EDIT.value)
        db_session.add(perm)
        user_perm = UserPerms(user_id=moderator.user_id, perm_id=1, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        token = create_access_token(moderator.user_id)

        response = await client.get(
            f"/images/2026-01-02-{protected_image.image_id}.png",
            cookies={"access_token": token},
        )
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers


class TestServeThumbnailEndpoint:
    """Tests for GET /thumbs/{filename} endpoint."""

    @pytest.fixture
    async def public_image(self, db_session: AsyncSession):
        """Create a public (ACTIVE) image."""
        image = Images(
            image_id=300,
            filename="2026-01-02-300",
            ext="jpeg",
            md5_hash="thumb123public",
            filesize=500,
            width=250,
            height=200,
            user_id=1,
            status=ImageStatus.ACTIVE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_thumbnail_returns_xaccel_with_internal_thumbs_path(
        self, client: AsyncClient, public_image: Images
    ):
        """Thumbnail endpoint returns X-Accel-Redirect with /internal/thumbs/ path."""
        response = await client.get(f"/thumbs/2026-01-02-{public_image.image_id}.jpeg")
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers
        # Thumbnails are always jpeg regardless of original format
        assert f"/internal/thumbs/{public_image.md5_hash}.jpeg" in response.headers["X-Accel-Redirect"]
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_media.py::TestServeImageEndpoint -v`
Expected: FAIL - Endpoints not registered, 404 for all requests

**Step 3: Implement endpoints and register router**

Update `app/api/v1/media.py` - add endpoints:

```python
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

    Args:
        filename: Image filename like "2026-01-02-1112196.png"
        db: Database session
        current_user: Authenticated user or None

    Returns:
        Response with X-Accel-Redirect header

    Raises:
        HTTPException 404: If image not found or user lacks permission
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
    Authentication: Cookie-based (access_token).

    Note: Thumbnails are always JPEG format regardless of original image format.

    Args:
        filename: Image filename like "2026-01-02-1112196.jpeg"
        db: Database session
        current_user: Authenticated user or None

    Returns:
        Response with X-Accel-Redirect header

    Raises:
        HTTPException 404: If image not found or user lacks permission
    """
    return await _serve_image(filename, "thumbs", db, current_user)


async def _serve_image(
    filename: str,
    image_type: str,  # "fullsize" or "thumbs"
    db: AsyncSession,
    current_user: Users | None,
) -> Response:
    """
    Internal handler for serving images.

    Args:
        filename: The requested filename
        image_type: Either "fullsize" or "thumbs"
        db: Database session
        current_user: Authenticated user or None

    Returns:
        Response with X-Accel-Redirect header

    Raises:
        HTTPException 404: If image not found or user lacks permission
    """
    # Parse image_id from filename
    image_id = parse_image_id_from_filename(filename)
    if image_id is None:
        raise HTTPException(status_code=404)

    # Fetch image from database
    image = await db.get(Images, image_id)
    if image is None:
        raise HTTPException(status_code=404)

    # Check visibility permission
    if not await can_view_image_file(image, current_user, db):
        # Return 404 (not 403) to avoid revealing existence of hidden images
        raise HTTPException(status_code=404)

    # Determine file extension for X-Accel-Redirect path
    if image_type == "thumbs":
        # Thumbnails are always JPEG
        ext = "jpeg"
    else:
        ext = get_extension_from_filename(filename)

    # Return X-Accel-Redirect for nginx to serve the file
    internal_path = f"/internal/{image_type}/{image.md5_hash}.{ext}"
    return Response(
        status_code=200,
        headers={"X-Accel-Redirect": internal_path},
    )
```

Update `app/main.py` - add media router at root level (after the API router):

```python
# Add after line 133 (app.include_router(api_v1_router, prefix="/api/v1"))
from app.api.v1.media import router as media_router  # noqa: E402

# Mount media routes at root level (not under /api/v1)
# These serve image files with permission checks via X-Accel-Redirect
app.include_router(media_router)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_media.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add app/api/v1/media.py app/main.py tests/api/v1/test_media.py
git commit -m "feat: add media endpoints with X-Accel-Redirect and permission checks"
```

---

## Task 4: Add Integration Test for Full Flow

**Files:**
- Modify: `tests/api/v1/test_media.py`

**Step 1: Write integration test covering full visibility matrix**

Add to `tests/api/v1/test_media.py`:

```python
class TestVisibilityMatrix:
    """Integration tests for the complete visibility matrix."""

    @pytest.fixture
    async def images_by_status(self, db_session: AsyncSession):
        """Create images with different statuses."""
        statuses = {
            "review": ImageStatus.REVIEW,
            "inappropriate": ImageStatus.INAPPROPRIATE,
            "repost": ImageStatus.REPOST,
            "other": ImageStatus.OTHER,
            "active": ImageStatus.ACTIVE,
            "spoiler": ImageStatus.SPOILER,
        }
        images = {}
        for name, status in statuses.items():
            image = Images(
                filename=f"2026-01-02-{400 + len(images)}",
                ext="png",
                md5_hash=f"matrix{name}hash",
                filesize=1000,
                width=100,
                height=100,
                user_id=1,  # Owned by testuser
                status=status,
            )
            db_session.add(image)
            await db_session.flush()
            images[name] = image
        await db_session.commit()
        return images

    async def test_anonymous_sees_public_statuses_only(
        self, client: AsyncClient, images_by_status: dict
    ):
        """Anonymous users can only see REPOST, ACTIVE, SPOILER."""
        public = ["repost", "active", "spoiler"]
        protected = ["review", "inappropriate", "other"]

        for name in public:
            img = images_by_status[name]
            response = await client.get(f"/images/2026-01-02-{img.image_id}.png")
            assert response.status_code == 200, f"Expected 200 for {name}, got {response.status_code}"

        for name in protected:
            img = images_by_status[name]
            response = await client.get(f"/images/2026-01-02-{img.image_id}.png")
            assert response.status_code == 404, f"Expected 404 for {name}, got {response.status_code}"

    async def test_owner_sees_all_statuses(
        self, client: AsyncClient, images_by_status: dict, db_session: AsyncSession
    ):
        """Image owner can see all their images regardless of status."""
        owner = await db_session.get(Users, 1)
        token = create_access_token(owner.user_id)

        for name, img in images_by_status.items():
            response = await client.get(
                f"/images/2026-01-02-{img.image_id}.png",
                cookies={"access_token": token},
            )
            assert response.status_code == 200, f"Owner should see {name}, got {response.status_code}"
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_media.py::TestVisibilityMatrix -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/api/v1/test_media.py
git commit -m "test: add visibility matrix integration tests"
```

---

## Task 5: Run Full Test Suite and Lint

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: PASS (all tests including new ones)

**Step 2: Run linter**

Run: `uv run ruff check app/services/image_visibility.py app/api/v1/media.py tests/unit/test_image_visibility.py tests/api/v1/test_media.py`
Expected: No errors

**Step 3: Run formatter**

Run: `uv run ruff format app/services/image_visibility.py app/api/v1/media.py tests/unit/test_image_visibility.py tests/api/v1/test_media.py`

**Step 4: Final commit if any formatting changes**

```bash
git add -A
git commit -m "chore: format code with ruff"
```

---

## Task 6: Document nginx Configuration

**Files:**
- Modify: `docs/plans/2026-01-02-image-visibility-protection-design.md` (already has nginx config)

**Step 1: Verify design doc has nginx config**

The design document already contains the nginx configuration. No additional changes needed, but the user should be reminded to:

1. Update nginx config to proxy `/images/` and `/thumbs/` to FastAPI
2. Add internal locations for X-Accel-Redirect
3. Substitute `STORAGE_PATH` environment variable in the config

**Step 2: Create a summary for the user**

After implementation is complete, provide summary of what nginx changes are needed.

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Visibility service + unit tests | `app/services/image_visibility.py`, `tests/unit/test_image_visibility.py` |
| 2 | Media router + parsing tests | `app/api/v1/media.py`, `tests/api/v1/test_media.py` |
| 3 | Media endpoints + integration | `app/api/v1/media.py`, `app/main.py`, `tests/api/v1/test_media.py` |
| 4 | Visibility matrix tests | `tests/api/v1/test_media.py` |
| 5 | Full test suite + lint | - |
| 6 | nginx config documentation | Design doc reference |

**Total estimated commits:** 5-6

"""Tests for media file serving endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.media import get_extension_from_filename, parse_image_id_from_filename
from app.config import ImageStatus
from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.image import Images
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


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

    async def test_public_image_anonymous_returns_xaccel(
        self, client: AsyncClient, public_image: Images
    ):
        """Public image returns X-Accel-Redirect for anonymous user."""
        response = await client.get(f"/images/2026-01-02-{public_image.image_id}.png")
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers
        assert (
            f"/internal/fullsize/{public_image.filename}.png"
            in response.headers["X-Accel-Redirect"]
        )

    async def test_protected_image_anonymous_returns_404(
        self, client: AsyncClient, protected_image: Images
    ):
        """Protected image returns 404 for anonymous user (not 403 to hide existence)."""
        response = await client.get(f"/images/2026-01-02-{protected_image.image_id}.png")
        assert response.status_code == 404

    async def test_protected_image_owner_returns_xaccel(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns X-Accel-Redirect for owner."""
        owner = await db_session.get(Users, 1)
        owner.active = 1  # Must be active to authenticate
        await db_session.commit()
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
        non_owner = await db_session.get(Users, 2)
        non_owner.active = 1  # Must be active to authenticate
        await db_session.commit()
        token = create_access_token(non_owner.user_id)
        response = await client.get(
            f"/images/2026-01-02-{protected_image.image_id}.png",
            cookies={"access_token": token},
        )
        assert response.status_code == 404

    async def test_protected_image_moderator_with_image_edit_returns_xaccel(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns X-Accel-Redirect for moderator with IMAGE_EDIT."""
        moderator = await db_session.get(Users, 2)
        moderator.active = 1  # Must be active to authenticate
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

    async def test_protected_image_reviewer_with_review_view_returns_xaccel(
        self, client: AsyncClient, protected_image: Images, db_session: AsyncSession
    ):
        """Protected image returns X-Accel-Redirect for user with REVIEW_VIEW."""
        reviewer = await db_session.get(Users, 2)
        reviewer.active = 1  # Must be active to authenticate
        perm = Perms(perm_id=2, title=Permission.REVIEW_VIEW.value)
        db_session.add(perm)
        user_perm = UserPerms(user_id=reviewer.user_id, perm_id=2, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        token = create_access_token(reviewer.user_id)
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
        assert (
            f"/internal/thumbs/{public_image.filename}.jpeg" in response.headers["X-Accel-Redirect"]
        )


class TestServeMediumEndpoint:
    """Tests for GET /medium/{filename} endpoint."""

    @pytest.fixture
    async def image_with_medium(self, db_session: AsyncSession):
        """Create a public image with medium variant available."""
        image = Images(
            image_id=400,
            filename="2026-01-02-400",
            ext="png",
            md5_hash="medium123hash",
            filesize=2000,
            width=1920,
            height=1080,
            user_id=1,
            status=ImageStatus.ACTIVE,
            medium=1,  # Medium variant exists
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.fixture
    async def image_without_medium(self, db_session: AsyncSession):
        """Create a public image without medium variant."""
        image = Images(
            image_id=401,
            filename="2026-01-02-401",
            ext="png",
            md5_hash="nomedium123hash",
            filesize=1000,
            width=800,
            height=600,
            user_id=1,
            status=ImageStatus.ACTIVE,
            medium=0,  # No medium variant
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_medium_returns_xaccel_when_variant_exists(
        self, client: AsyncClient, image_with_medium: Images
    ):
        """Medium endpoint returns X-Accel-Redirect when variant exists."""
        response = await client.get(f"/medium/2026-01-02-{image_with_medium.image_id}.png")
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers
        assert f"/internal/medium/{image_with_medium.filename}.png" in response.headers["X-Accel-Redirect"]

    async def test_medium_returns_404_when_variant_missing(
        self, client: AsyncClient, image_without_medium: Images
    ):
        """Medium endpoint returns 404 when variant doesn't exist."""
        response = await client.get(f"/medium/2026-01-02-{image_without_medium.image_id}.png")
        assert response.status_code == 404


class TestServeLargeEndpoint:
    """Tests for GET /large/{filename} endpoint."""

    @pytest.fixture
    async def image_with_large(self, db_session: AsyncSession):
        """Create a public image with large variant available."""
        image = Images(
            image_id=500,
            filename="2026-01-02-500",
            ext="jpeg",
            md5_hash="large123hash",
            filesize=5000,
            width=4000,
            height=3000,
            user_id=1,
            status=ImageStatus.ACTIVE,
            large=1,  # Large variant exists
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.fixture
    async def image_without_large(self, db_session: AsyncSession):
        """Create a public image without large variant."""
        image = Images(
            image_id=501,
            filename="2026-01-02-501",
            ext="jpeg",
            md5_hash="nolarge123hash",
            filesize=1000,
            width=1200,
            height=800,
            user_id=1,
            status=ImageStatus.ACTIVE,
            large=0,  # No large variant
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_large_returns_xaccel_when_variant_exists(
        self, client: AsyncClient, image_with_large: Images
    ):
        """Large endpoint returns X-Accel-Redirect when variant exists."""
        response = await client.get(f"/large/2026-01-02-{image_with_large.image_id}.jpeg")
        assert response.status_code == 200
        assert "X-Accel-Redirect" in response.headers
        assert f"/internal/large/{image_with_large.filename}.jpeg" in response.headers["X-Accel-Redirect"]

    async def test_large_returns_404_when_variant_missing(
        self, client: AsyncClient, image_without_large: Images
    ):
        """Large endpoint returns 404 when variant doesn't exist."""
        response = await client.get(f"/large/2026-01-02-{image_without_large.image_id}.jpeg")
        assert response.status_code == 404


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
            assert response.status_code == 200, (
                f"Expected 200 for {name}, got {response.status_code}"
            )

        for name in protected:
            img = images_by_status[name]
            response = await client.get(f"/images/2026-01-02-{img.image_id}.png")
            assert response.status_code == 404, (
                f"Expected 404 for {name}, got {response.status_code}"
            )

    async def test_owner_sees_all_statuses(
        self, client: AsyncClient, images_by_status: dict, db_session: AsyncSession
    ):
        """Image owner can see all their images regardless of status."""
        owner = await db_session.get(Users, 1)
        owner.active = 1  # Ensure user is active for auth
        await db_session.commit()
        token = create_access_token(owner.user_id)

        for name, img in images_by_status.items():
            response = await client.get(
                f"/images/2026-01-02-{img.image_id}.png",
                cookies={"access_token": token},
            )
            assert response.status_code == 200, (
                f"Owner should see {name}, got {response.status_code}"
            )

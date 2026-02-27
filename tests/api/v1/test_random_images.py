"""Tests for GET /api/v1/images/random endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.security import create_access_token
from app.models.image import Images
from app.models.user import Users


@pytest.mark.api
class TestRandomImagesRedirect:
    """Tests for GET /api/v1/images/random endpoint."""

    async def test_redirects_to_valid_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user,
    ):
        """Test that /images/random redirects to a valid page number."""
        # Create 25 images so we have 2 pages at per_page=20
        for i in range(25):
            img = Images(
                filename=f"random_test_{i}",
                ext="jpg",
                width=100,
                height=100,
                status=ImageStatus.ACTIVE,
                user_id=test_user.user_id,
            )
            db_session.add(img)
        await db_session.commit()

        response = await client.get("/api/v1/images/random", follow_redirects=False)

        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("/?page=")
        page = int(location.split("page=")[1])
        assert 1 <= page <= 2

    async def test_respects_per_page_param(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user,
    ):
        """Test that per_page param affects the total page count for the redirect."""
        # Create 10 images so we have 2 pages at per_page=5
        for i in range(10):
            img = Images(
                filename=f"per_page_test_{i}",
                ext="jpg",
                width=100,
                height=100,
                status=ImageStatus.ACTIVE,
                user_id=test_user.user_id,
            )
            db_session.add(img)
        await db_session.commit()

        response = await client.get(
            "/api/v1/images/random?per_page=5", follow_redirects=False
        )

        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("/?page=")
        page = int(location.split("page=")[1])
        assert 1 <= page <= 2

    async def test_non_public_images_excluded_for_anonymous(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user,
    ):
        """Test that anonymous users cannot see non-public images."""
        # Create only non-public images (OTHER is not in PUBLIC_IMAGE_STATUSES)
        for i in range(5):
            img = Images(
                filename=f"non_public_test_{i}",
                ext="jpg",
                width=100,
                height=100,
                status=ImageStatus.OTHER,
                user_id=test_user.user_id,
            )
            db_session.add(img)
        await db_session.commit()

        response = await client.get("/api/v1/images/random", follow_redirects=False)

        assert response.status_code == 404

    async def test_respects_user_images_per_page_setting(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test that authenticated user's images_per_page setting is used for page calculation."""
        # Create user with images_per_page=5
        user = Users(
            username="random_page_user",
            password="hashed_password",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="random_page@example.com",
            active=1,
            images_per_page=5,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create 10 images → 2 pages at per_page=5, but only 1 page at per_page=20
        for i in range(10):
            img = Images(
                filename=f"user_pp_test_{i}",
                ext="jpg",
                width=100,
                height=100,
                status=ImageStatus.ACTIVE,
                user_id=user.user_id,
            )
            db_session.add(img)
        await db_session.commit()

        access_token = create_access_token(user.user_id)

        # Run multiple times to catch the case where random picks page 2
        # (which would be invalid if per_page=20 were used instead of 5)
        seen_pages = set()
        for _ in range(50):
            response = await client.get(
                "/api/v1/images/random",
                headers={"Authorization": f"Bearer {access_token}"},
                follow_redirects=False,
            )
            assert response.status_code == 302
            location = response.headers["location"]
            page = int(location.split("page=")[1])
            seen_pages.add(page)

        # With 10 images and images_per_page=5, max page should be 2
        # If the bug exists (using default 20), max page would be 1
        assert max(seen_pages) <= 2
        # With 50 attempts and only 2 possible pages, we should see page 2
        assert 2 in seen_pages, (
            f"With 10 images and images_per_page=5, page 2 should be reachable. "
            f"Only saw pages: {seen_pages}"
        )

    async def test_returns_404_when_no_images(
        self,
        client: AsyncClient,
    ):
        """Test that /images/random returns 404 when no visible images exist."""
        response = await client.get("/api/v1/images/random", follow_redirects=False)

        assert response.status_code == 404

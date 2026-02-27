"""Tests for GET /api/v1/images/random endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.models.image import Images


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
        assert location.startswith("/api/v1/images?page=")
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
        assert location.startswith("/api/v1/images?page=")
        page = int(location.split("page=")[1])
        assert 1 <= page <= 2

    async def test_non_public_images_excluded_for_anonymous(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user,
    ):
        """Test that anonymous users cannot see non-public images."""
        # Create only non-public images (status=0 is not in PUBLIC_IMAGE_STATUSES)
        for i in range(5):
            img = Images(
                filename=f"non_public_test_{i}",
                ext="jpg",
                width=100,
                height=100,
                status=0,
                user_id=test_user.user_id,
            )
            db_session.add(img)
        await db_session.commit()

        response = await client.get("/api/v1/images/random", follow_redirects=False)

        assert response.status_code == 404

    async def test_returns_404_when_no_images(
        self,
        client: AsyncClient,
    ):
        """Test that /images/random returns 404 when no visible images exist."""
        response = await client.get("/api/v1/images/random", follow_redirects=False)

        assert response.status_code == 404

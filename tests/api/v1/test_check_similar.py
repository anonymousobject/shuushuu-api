"""Tests for POST /api/v1/images/check-similar endpoint."""

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import Users
from app.schemas.image import SimilarImageResult


@pytest.fixture
async def verified_user(db_session: AsyncSession) -> Users:
    """Create a verified user for testing."""
    user = Users(
        username="checker",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="checker@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def auth_client(client: AsyncClient, verified_user: Users) -> AsyncClient:
    """Authenticated client."""
    access_token = create_access_token(verified_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


def _fake_image_bytes() -> bytes:
    """Create a minimal valid JPEG for tests."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_similar_result(image_id: int, score: float) -> SimilarImageResult:
    """Build a SimilarImageResult for test assertions."""
    return SimilarImageResult(
        image_id=image_id,
        filename=f"2025-01-01-{image_id}",
        ext="jpg",
        md5_hash="fakehash",
        filesize=1000,
        width=100,
        height=100,
        rating=0.0,
        user_id=1,
        date_added="2025-01-01T00:00:00",
        status=1,
        locked=0,
        posts=0,
        favorites=0,
        bayesian_rating=0.0,
        num_ratings=0,
        medium=0,
        large=0,
        similarity_score=score,
    )


@pytest.mark.api
class TestCheckSimilar:
    """Tests for the check-similar endpoint."""

    @pytest.mark.asyncio
    async def test_returns_similar_images(self, auth_client: AsyncClient):
        """Successful similarity check returns hydrated results."""
        hydrated = [
            _make_similar_result(42, 95.5),
            _make_similar_result(99, 80.0),
        ]

        with (
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[
                    {"image_id": 42, "score": 95.5},
                    {"image_id": 99, "score": 80.0},
                ],
            ),
            patch(
                "app.api.v1.images._hydrate_similar_images",
                new_callable=AsyncMock,
                return_value=hydrated,
            ),
            patch("app.api.v1.images.validate_image_file"),
            patch("app.api.v1.images.create_thumbnail"),
            patch(
                "app.api.v1.images.check_similarity_rate_limit",
                new_callable=AsyncMock,
            ),
            patch("pathlib.Path.exists", return_value=True),
        ):
            response = await auth_client.post(
                "/api/v1/images/check-similar",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        data = response.json()
        assert "similar_images" in data
        assert len(data["similar_images"]) == 2
        assert data["similar_images"][0]["image_id"] == 42
        assert data["similar_images"][0]["similarity_score"] == 95.5

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_matches(self, auth_client: AsyncClient):
        """Returns empty list when IQDB finds no similar images."""
        with (
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.validate_image_file"),
            patch("app.api.v1.images.create_thumbnail"),
            patch(
                "app.api.v1.images.check_similarity_rate_limit",
                new_callable=AsyncMock,
            ),
            patch("pathlib.Path.exists", return_value=True),
        ):
            response = await auth_client.post(
                "/api/v1/images/check-similar",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["similar_images"] == []

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client: AsyncClient):
        """Unauthenticated request returns 401."""
        response = await client.post(
            "/api/v1/images/check-similar",
            files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_non_image_file(self, auth_client: AsyncClient):
        """Non-image file returns 400."""
        with patch(
            "app.api.v1.images.check_similarity_rate_limit",
            new_callable=AsyncMock,
        ):
            response = await auth_client.post(
                "/api/v1/images/check-similar",
                files={"file": ("test.txt", b"not an image", "text/plain")},
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rate_limit_returns_429(self, auth_client: AsyncClient):
        """Rate-limited request returns 429."""
        from fastapi import HTTPException

        with patch(
            "app.api.v1.images.check_similarity_rate_limit",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": "60"},
            ),
        ):
            response = await auth_client.post(
                "/api/v1/images/check-similar",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
            )
        assert response.status_code == 429

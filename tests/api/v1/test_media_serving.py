"""Tests for /images/* media-serving endpoint R2 branches."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.core.r2_client import reset_r2_storage
from app.core.r2_constants import R2Location
from app.core.security import create_access_token
from app.models.image import Images
from app.models.user import Users


@pytest.mark.api
class TestMediaServingR2:
    @pytest.fixture(autouse=True)
    def _reset_r2(self):
        reset_r2_storage()
        yield
        reset_r2_storage()

    @pytest.fixture
    async def public_r2_image(self, db_session: AsyncSession):
        """ACTIVE image in the public R2 bucket."""
        image = Images(
            image_id=701,
            filename="2026-04-18-701",
            ext="jpg",
            md5_hash="r2public701hash",
            filesize=1000,
            width=800,
            height=600,
            user_id=1,
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.PUBLIC,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.fixture
    async def private_r2_image(self, db_session: AsyncSession):
        """REVIEW image in the private R2 bucket (protected status + PRIVATE location)."""
        image = Images(
            image_id=702,
            filename="2026-04-18-702",
            ext="jpg",
            md5_hash="r2private702hash",
            filesize=1000,
            width=800,
            height=600,
            user_id=1,
            status=ImageStatus.REVIEW,
            r2_location=R2Location.PRIVATE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.fixture
    async def local_image(self, db_session: AsyncSession):
        """ACTIVE image not yet synced to R2 (r2_location=NONE)."""
        image = Images(
            image_id=703,
            filename="2026-04-18-703",
            ext="jpg",
            md5_hash="r2local703hash",
            filesize=1000,
            width=800,
            height=600,
            user_id=1,
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.NONE,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    async def test_public_bucket_302s_to_cdn(
        self, client: AsyncClient, public_r2_image: Images, monkeypatch
    ):
        """status=ACTIVE, r2_location=PUBLIC, R2 on → 302 to CDN URL with no-store."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")

        response = await client.get(
            f"/images/2026-04-18-{public_r2_image.image_id}.jpg",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].startswith("https://cdn.example.com/fullsize/")
        assert response.headers["Cache-Control"] == "no-store"

    async def test_private_bucket_302s_to_presigned(
        self, client: AsyncClient, private_r2_image: Images, db_session: AsyncSession, monkeypatch
    ):
        """Protected status + PRIVATE location → 302 to presigned URL for owner."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        owner = await db_session.get(Users, 1)
        owner.active = 1
        await db_session.commit()
        token = create_access_token(owner.user_id)

        mock_r2 = AsyncMock()
        mock_r2.generate_presigned_url = AsyncMock(
            return_value="https://presigned.example.com/foo?sig=xxx"
        )
        with patch("app.api.v1.media.get_r2_storage", return_value=mock_r2):
            response = await client.get(
                f"/images/2026-04-18-{private_r2_image.image_id}.jpg",
                follow_redirects=False,
                cookies={"access_token": token},
            )
        assert response.status_code == 302
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers.get("Location")
        # Verify routing wired up the correct bucket, key, and TTL.
        mock_r2.generate_presigned_url.assert_awaited_once_with(
            bucket=settings.R2_PRIVATE_BUCKET,
            key=f"fullsize/{private_r2_image.filename}.{private_r2_image.ext}",
            ttl=settings.R2_PRESIGN_TTL_SECONDS,
        )

    async def test_location_none_falls_back_to_xaccel(
        self, client: AsyncClient, local_image: Images, monkeypatch
    ):
        """r2_location=NONE → X-Accel-Redirect to /internal/ (unchanged behaviour)."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        response = await client.get(
            f"/images/2026-04-18-{local_image.image_id}.jpg",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.headers["X-Accel-Redirect"].startswith("/internal/fullsize/")

    async def test_r2_disabled_always_xaccel(
        self, client: AsyncClient, public_r2_image: Images, monkeypatch
    ):
        """R2_ENABLED=false → X-Accel-Redirect regardless of r2_location."""
        monkeypatch.setattr(settings, "R2_ENABLED", False)

        response = await client.get(
            f"/images/2026-04-18-{public_r2_image.image_id}.jpg",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.headers["X-Accel-Redirect"].startswith("/internal/")

    async def test_permission_check_still_runs(
        self, client: AsyncClient, private_r2_image: Images, monkeypatch
    ):
        """Anonymous request for a protected image returns 404 before any R2 logic."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        response = await client.get(
            f"/images/2026-04-18-{private_r2_image.image_id}.jpg",
        )
        assert response.status_code == 404

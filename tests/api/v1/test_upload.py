"""Tests for the image upload route."""

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import Users
from app.schemas.image import SimilarImageResult


@pytest.fixture
async def verified_user(db_session: AsyncSession) -> Users:
    """Create a verified user for upload testing."""
    user = Users(
        username="uploader",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="uploader@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def upload_client(client: AsyncClient, verified_user: Users) -> AsyncClient:
    """Authenticated client with a verified user."""
    access_token = create_access_token(verified_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


def _fake_image_bytes() -> bytes:
    """Create a minimal valid JPEG for upload tests."""
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


def _mock_save_uploaded_image(md5: str = "abc123unique"):
    """Create an AsyncMock for save_uploaded_image that returns a fake path."""
    fake_path = Path("/tmp/fake-upload.jpg")

    async def _save(file, storage_path, image_id):
        # Create the fake file so cleanup code doesn't error
        fake_path.touch()
        return fake_path, "jpg", md5

    return patch("app.api.v1.images.save_uploaded_image", side_effect=_save)


class TestUploadIQDBDuplicateDetection:
    """Tests for IQDB near-duplicate detection during upload."""

    @pytest.mark.asyncio
    async def test_upload_returns_409_when_similar_images_found(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload returns 409 with similar images when IQDB finds near-duplicates."""
        hydrated = [
            _make_similar_result(42, 95.5),
            _make_similar_result(99, 91.0),
        ]

        with (
            _mock_save_uploaded_image(),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[{"image_id": 42, "score": 95.5}, {"image_id": 99, "score": 91.0}],
            ),
            patch(
                "app.api.v1.images._hydrate_similar_images",
                new_callable=AsyncMock,
                return_value=hydrated,
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 409
        data = response.json()
        assert "similar_images" in data
        assert len(data["similar_images"]) == 2
        assert data["similar_images"][0]["image_id"] == 42
        assert data["similar_images"][0]["similarity_score"] == 95.5
        assert data["similar_images"][1]["image_id"] == 99
        assert data["similar_images"][1]["similarity_score"] == 91.0

    @pytest.mark.asyncio
    async def test_upload_succeeds_with_confirm_similar(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds when confirm_similar=true, skipping IQDB check."""
        mock_iqdb = AsyncMock(return_value=[])

        with (
            _mock_save_uploaded_image("abc123unique2"),
            patch("app.api.v1.images.check_iqdb_similarity", mock_iqdb),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": "", "confirm_similar": "true"},
            )

        assert response.status_code == 201
        # IQDB should not have been called
        mock_iqdb.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_succeeds_when_no_iqdb_matches(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds normally when IQDB finds no near-duplicates."""
        with (
            _mock_save_uploaded_image("abc123unique3"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        data = response.json()
        assert "similar_images" not in data

    @pytest.mark.asyncio
    async def test_upload_stores_and_returns_miscmeta(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload with miscmeta parameter stores it and returns it in the response."""
        with (
            _mock_save_uploaded_image("abc123unique4"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": "", "miscmeta": "pixiv: 12345"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["image"]["miscmeta"] == "pixiv: 12345"


class TestUploadMLTagSuggestions:
    """Tests for ML tag suggestion job enqueueing on upload."""

    @pytest.mark.asyncio
    async def test_upload_enqueues_ml_job_when_flag_enabled(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """When ML_TAG_SUGGESTIONS_ENABLED=True, upload enqueues generate_ml_tag_suggestions."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        enqueue_mock = AsyncMock()

        with (
            _mock_save_uploaded_image("ml_enqueue_on"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        ml_calls = [
            call
            for call in enqueue_mock.call_args_list
            if call.args and call.args[0] == "generate_ml_tag_suggestions"
        ]
        assert len(ml_calls) == 1, f"Expected 1 ml job call, got {len(ml_calls)}: {enqueue_mock.call_args_list}"
        image_id = response.json()["image"]["image_id"]
        assert ml_calls[0].kwargs.get("image_id") == image_id
        assert ml_calls[0].kwargs.get("_defer_by") is None  # runs immediately, no defer

    @pytest.mark.asyncio
    async def test_upload_does_not_enqueue_ml_job_when_flag_disabled(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """When ML_TAG_SUGGESTIONS_ENABLED=False (default), upload does not enqueue the ml job."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)

        enqueue_mock = AsyncMock()

        with (
            _mock_save_uploaded_image("ml_enqueue_off"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        ml_calls = [
            call
            for call in enqueue_mock.call_args_list
            if call.args and call.args[0] == "generate_ml_tag_suggestions"
        ]
        assert len(ml_calls) == 0, f"Expected no ml job calls, got: {enqueue_mock.call_args_list}"

    @pytest.mark.asyncio
    async def test_upload_succeeds_when_ml_enqueue_fails(
        self, upload_client: AsyncClient, verified_user: Users, monkeypatch
    ):
        """Upload still succeeds (201) when the ml tag suggestion enqueue raises an exception."""
        from app.config import settings

        monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

        def _side_effect(job_name, **kwargs):
            if job_name == "generate_ml_tag_suggestions":
                raise RuntimeError("arq unavailable")
            return None

        enqueue_mock = AsyncMock(side_effect=_side_effect)

        with (
            _mock_save_uploaded_image("ml_enqueue_fail"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", enqueue_mock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
            )

        assert response.status_code == 201
        non_ml_calls = [
            c for c in enqueue_mock.call_args_list
            if not (c.args and c.args[0] == "generate_ml_tag_suggestions")
        ]
        assert non_ml_calls, "other enqueue jobs should still have been called"


class TestUploadClientIPHandling:
    """Tests for client IP header handling on upload."""

    @pytest.mark.asyncio
    async def test_upload_succeeds_for_ipv6_client(
        self, upload_client: AsyncClient, verified_user: Users
    ):
        """Upload succeeds when X-Forwarded-For carries an IPv6 address.

        Cloudflare forwards the real client IP via X-Forwarded-For; IPv6
        addresses are up to 39 chars (45 with zone-id), so the Images.ip
        column must accommodate them.
        """
        ipv6 = "2600:6c63:ff0:6810:c042:21d5:bfed:9bae"
        with (
            _mock_save_uploaded_image("ipv6upload01"),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.api.v1.images.get_image_dimensions", return_value=(100, 100)),
            patch("app.api.v1.images.enqueue_job", new_callable=AsyncMock),
        ):
            response = await upload_client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                data={"tag_ids": "", "caption": ""},
                headers={"X-Forwarded-For": ipv6},
            )

        assert response.status_code == 201, response.text

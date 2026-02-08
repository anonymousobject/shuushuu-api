"""Tests for the similar images endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestSimilarImages:
    """Tests for GET /images/{image_id}/similar endpoint."""

    @pytest.mark.asyncio
    async def test_similar_images_not_found(self, client: AsyncClient):
        """Returns 404 for non-existent image."""
        response = await client.get("/api/v1/images/999999999/similar")
        assert response.status_code == 404
        assert response.json()["detail"] == "Image not found"

    @pytest.mark.asyncio
    async def test_similar_images_no_thumbnail(
        self, client: AsyncClient, test_image, db_session
    ):
        """Returns 404 if thumbnail doesn't exist."""
        # The test_image fixture creates a DB record but no actual file
        with patch("app.api.v1.images.FilePath.exists", return_value=False):
            response = await client.get(f"/api/v1/images/{test_image.image_id}/similar")

        assert response.status_code == 404
        assert "thumbnail not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_similar_images_empty_results(
        self, client: AsyncClient, test_image, db_session
    ):
        """Returns empty list when IQDB finds no similar images."""
        with (
            patch("app.api.v1.images.FilePath.exists", return_value=True),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = await client.get(f"/api/v1/images/{test_image.image_id}/similar")

        assert response.status_code == 200
        data = response.json()
        assert data["query_image_id"] == test_image.image_id
        assert data["similar_images"] == []

    @pytest.mark.asyncio
    async def test_similar_images_excludes_query_image(
        self, client: AsyncClient, test_image, db_session
    ):
        """Query image itself is excluded from results."""
        # IQDB returns the query image with high score
        iqdb_response = [
            {"image_id": test_image.image_id, "score": 98.5},
        ]

        with (
            patch("app.api.v1.images.FilePath.exists", return_value=True),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=iqdb_response,
            ),
        ):
            response = await client.get(f"/api/v1/images/{test_image.image_id}/similar")

        assert response.status_code == 200
        data = response.json()
        assert data["similar_images"] == []

    @pytest.mark.asyncio
    async def test_similar_images_returns_matches(
        self, client: AsyncClient, test_image, another_test_image, db_session
    ):
        """Returns similar images with scores when IQDB finds matches."""
        # IQDB returns another image as similar
        iqdb_response = [
            {"image_id": test_image.image_id, "score": 98.5},  # Query image (excluded)
            {"image_id": another_test_image.image_id, "score": 85.3},
        ]

        with (
            patch("app.api.v1.images.FilePath.exists", return_value=True),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=iqdb_response,
            ),
        ):
            response = await client.get(f"/api/v1/images/{test_image.image_id}/similar")

        assert response.status_code == 200
        data = response.json()
        assert data["query_image_id"] == test_image.image_id
        assert len(data["similar_images"]) == 1
        assert data["similar_images"][0]["image_id"] == another_test_image.image_id
        assert data["similar_images"][0]["similarity_score"] == 85.3

    @pytest.mark.asyncio
    async def test_similar_images_ordered_by_score(
        self, client: AsyncClient, test_image, test_images_batch, db_session
    ):
        """Results are ordered by similarity score descending."""
        # Create IQDB response with multiple images in non-sorted order
        batch_ids = [img.image_id for img in test_images_batch[:3]]
        iqdb_response = [
            {"image_id": batch_ids[0], "score": 70.0},
            {"image_id": batch_ids[1], "score": 90.0},
            {"image_id": batch_ids[2], "score": 80.0},
        ]

        with (
            patch("app.api.v1.images.FilePath.exists", return_value=True),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=iqdb_response,
            ),
        ):
            response = await client.get(f"/api/v1/images/{test_image.image_id}/similar")

        assert response.status_code == 200
        data = response.json()
        scores = [img["similarity_score"] for img in data["similar_images"]]
        assert scores == [90.0, 80.0, 70.0]

    @pytest.mark.asyncio
    async def test_similar_images_threshold_parameter(
        self, client: AsyncClient, test_image, db_session
    ):
        """Threshold parameter is passed to IQDB service."""
        with (
            patch("app.api.v1.images.FilePath.exists", return_value=True),
            patch(
                "app.api.v1.images.check_iqdb_similarity",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_iqdb,
        ):
            response = await client.get(
                f"/api/v1/images/{test_image.image_id}/similar?threshold=75"
            )

        assert response.status_code == 200
        mock_iqdb.assert_called_once()
        # Check threshold was passed (third positional arg or keyword arg)
        call_kwargs = mock_iqdb.call_args
        assert call_kwargs.kwargs.get("threshold") == 75


class TestIQDBService:
    """Unit tests for the IQDB service functions."""

    @pytest.mark.asyncio
    async def test_check_iqdb_similarity_success(self, db_session):
        """check_iqdb_similarity returns filtered results on success."""
        from app.services.iqdb import check_iqdb_similarity
        from pathlib import Path
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"post_id": 123, "score": 95.0},
            {"post_id": 456, "score": 85.0},
            {"post_id": 789, "score": 50.0},  # Below threshold of 80
        ]

        with (
            patch("app.services.iqdb.open", create=True),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Pass explicit threshold to test filtering
            result = await check_iqdb_similarity(
                Path("/fake/path.jpg"), db_session, threshold=80
            )

        # Threshold is 80, so only first two should be returned
        assert len(result) == 2
        assert result[0]["image_id"] == 123
        assert result[0]["score"] == 95.0
        assert result[1]["image_id"] == 456

    @pytest.mark.asyncio
    async def test_check_iqdb_similarity_unavailable(self, db_session):
        """Returns empty list when IQDB is unavailable."""
        from app.services.iqdb import check_iqdb_similarity
        from pathlib import Path
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 500

        with (
            patch("app.services.iqdb.open", create=True),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await check_iqdb_similarity(Path("/fake/path.jpg"), db_session)

        assert result == []

    @pytest.mark.asyncio
    async def test_check_iqdb_similarity_timeout(self, db_session):
        """Returns empty list on timeout."""
        from app.services.iqdb import check_iqdb_similarity
        from pathlib import Path
        import httpx

        with (
            patch("app.services.iqdb.open", create=True),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await check_iqdb_similarity(Path("/fake/path.jpg"), db_session)

        assert result == []

    @pytest.mark.asyncio
    async def test_check_iqdb_similarity_file_not_found(self, db_session):
        """Returns empty list when image file doesn't exist."""
        from app.services.iqdb import check_iqdb_similarity
        from pathlib import Path

        result = await check_iqdb_similarity(
            Path("/nonexistent/path/image.jpg"), db_session
        )
        assert result == []


class TestIQDBUploadThreshold:
    """Tests configuration for IQDB upload duplicate detection threshold."""

    def test_upload_threshold_config_for_near_duplicates(self):
        """IQDB_UPLOAD_THRESHOLD is configured for near-duplicate detection."""
        from app.config import settings

        # The upload duplicate detection should only treat very similar matches
        # as duplicates, so the threshold must be high (e.g., >= 90).
        assert settings.IQDB_UPLOAD_THRESHOLD >= 90.0

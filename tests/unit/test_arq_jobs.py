"""Tests for arq background jobs."""

import pytest
from pathlib import Path as FilePath
from unittest.mock import AsyncMock, Mock, patch

from app.models.image import VariantStatus
from app.tasks.image_jobs import create_thumbnail_job, create_variant_job


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_thumbnail_job_success():
    """Test successful thumbnail creation job."""
    # Arrange
    ctx = {"job_try": 1}
    image_id = 123
    source_path = "/test/image.jpg"
    ext = "jpg"
    storage_path = "/test/storage"

    # Mock the image processing function (imported inside the job function)
    with patch("app.services.image_processing.create_thumbnail") as mock_create:
        # Act
        result = await create_thumbnail_job(ctx, image_id, source_path, ext, storage_path)

        # Assert
        assert result["success"] is True
        assert "thumbnail_path" in result
        mock_create.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_variant_job_sets_ready_on_success():
    """Variant job updates DB to READY when variant is created successfully."""
    ctx = {"job_try": 1}

    with (
        patch("app.services.image_processing._create_variant", return_value=True),
        patch(
            "app.services.image_processing._update_image_variant_field", new_callable=AsyncMock
        ) as mock_update,
    ):
        result = await create_variant_job(
            ctx,
            image_id=42,
            source_path="/test/image.jpg",
            ext="jpg",
            storage_path="/test/storage",
            width=2000,
            height=1500,
            variant_type="medium",
        )

    assert result["success"] is True
    assert result["created"] is True
    mock_update.assert_called_once_with(42, "medium", VariantStatus.READY)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_variant_job_sets_none_when_not_needed():
    """Variant job updates DB to NONE when image is too small for a variant."""
    ctx = {"job_try": 1}

    with (
        patch("app.services.image_processing._create_variant", return_value=False),
        patch(
            "app.services.image_processing._update_image_variant_field", new_callable=AsyncMock
        ) as mock_update,
    ):
        result = await create_variant_job(
            ctx,
            image_id=42,
            source_path="/test/image.jpg",
            ext="jpg",
            storage_path="/test/storage",
            width=500,
            height=400,
            variant_type="medium",
        )

    assert result["success"] is True
    assert result["created"] is False
    mock_update.assert_called_once_with(42, "medium", VariantStatus.NONE)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_thumbnail_job_retry_on_failure():
    """Test thumbnail job retries on failure."""
    from arq import Retry

    # Arrange
    ctx = {"job_try": 1}

    # Mock to raise exception
    with patch("app.services.image_processing.create_thumbnail") as mock_create:
        mock_create.side_effect = Exception("Image processing failed")

        # Act & Assert
        with pytest.raises(Retry):
            await create_thumbnail_job(ctx, 123, "/test/image.jpg", "jpg", "/test/storage")

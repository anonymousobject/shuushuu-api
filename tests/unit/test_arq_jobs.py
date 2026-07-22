"""Tests for arq background jobs."""

import hashlib
import pytest
from pathlib import Path as FilePath
from unittest.mock import AsyncMock, Mock, patch

from app.models.image import VariantStatus
from app.tasks.image_jobs import create_thumbnail_job, create_variant_job
from app.tasks.worker import WorkerSettings, _check_lockfile_freshness


@pytest.mark.unit
def test_worker_has_review_deadline_cron_job():
    """Ensure process_review_deadlines is registered as an arq cron job."""
    cron_func_names = [job.coroutine.__name__ for job in WorkerSettings.cron_jobs]
    assert "process_review_deadlines" in cron_func_names


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
        assert result["thumbnail_path"].endswith(".webp")
        assert ".jpeg" not in result["thumbnail_path"]
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
async def test_create_variant_job_retries_on_failure():
    """Variant job raises Retry when _create_variant raises an exception."""
    from arq import Retry

    ctx = {"job_try": 1}

    with patch(
        "app.services.image_processing._create_variant",
        side_effect=Exception("PIL error"),
    ):
        with pytest.raises(Retry):
            await create_variant_job(
                ctx,
                image_id=42,
                source_path="/test/image.jpg",
                ext="jpg",
                storage_path="/test/storage",
                width=2000,
                height=1500,
                variant_type="medium",
            )


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


# ---------------------------------------------------------------------------
# Lockfile freshness check
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_lockfile_check_passes_when_hash_matches(tmp_path):
    """No error when the mounted uv.lock hash matches the baked-in hash."""
    lock_content = b'lockfile content'
    lock_path = tmp_path / "uv.lock"
    hash_path = tmp_path / ".uv-lock-hash"
    lock_path.write_bytes(lock_content)
    hash_path.write_text(hashlib.sha256(lock_content).hexdigest())

    logger = Mock()
    with patch("app.tasks.worker.Path") as mock_path_cls:
        def _path_side_effect(p):
            s = str(p)
            if s.endswith(".uv-lock-hash"):
                return hash_path
            if s.endswith("uv.lock"):
                return lock_path
            return p

        mock_path_cls.side_effect = _path_side_effect

        # Should not raise
        _check_lockfile_freshness(logger)

    logger.error.assert_not_called()


@pytest.mark.unit
def test_lockfile_check_exits_when_hash_differs(tmp_path):
    """SystemExit when the mounted uv.lock differs from the baked-in hash."""
    lock_content = b'new lockfile content'
    old_hash = hashlib.sha256(b'old lockfile content').hexdigest()

    lock_path = tmp_path / "uv.lock"
    hash_path = tmp_path / ".uv-lock-hash"
    lock_path.write_bytes(lock_content)
    hash_path.write_text(old_hash)

    logger = Mock()
    with patch("app.tasks.worker.Path") as mock_path_cls:
        def _path_side_effect(p):
            s = str(p)
            if s.endswith(".uv-lock-hash"):
                return hash_path
            if s.endswith("uv.lock"):
                return lock_path
            return p

        mock_path_cls.side_effect = _path_side_effect

        with pytest.raises(SystemExit) as exc_info:
            _check_lockfile_freshness(logger)

    assert "Rebuild" in str(exc_info.value)
    assert "uv.lock" in str(exc_info.value)
    logger.error.assert_called_once()
    assert logger.error.call_args.args[0] == "worker_lockfile_mismatch"


@pytest.mark.unit
def test_lockfile_check_skips_when_no_hash_file(tmp_path):
    """No error when the image predates the hash file (backward compatible)."""
    lock_path = tmp_path / "uv.lock"
    lock_path.write_bytes(b'some lockfile')

    logger = Mock()
    with patch("app.tasks.worker.Path") as mock_path_cls:
        def _path_side_effect(p):
            s = str(p)
            if s.endswith(".uv-lock-hash"):
                return tmp_path / ".uv-lock-hash"  # does not exist
            if s.endswith("uv.lock"):
                return lock_path
            return p

        mock_path_cls.side_effect = _path_side_effect

        # Should not raise — hash file missing means older image
        _check_lockfile_freshness(logger)

    logger.error.assert_not_called()

"""Tests for arq background jobs."""

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.image import VariantStatus
from app.tasks.image_jobs import create_thumbnail_job, create_variant_job
from app.tasks.lockfile_check import check_lockfile_freshness
from app.tasks.worker import WorkerSettings


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

    with (
        patch(
            "app.services.image_processing._create_variant",
            side_effect=Exception("PIL error"),
        ),
        pytest.raises(Retry),
    ):
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
    lock_content = b"lockfile content"
    lock_path = tmp_path / "uv.lock"
    hash_path = tmp_path / ".uv-lock-hash"
    lock_path.write_bytes(lock_content)
    hash_path.write_text(hashlib.sha256(lock_content).hexdigest())

    logger = Mock()
    with (
        patch("app.tasks.lockfile_check.HASH_FILE", hash_path),
        patch("app.tasks.lockfile_check.LOCK_FILE", lock_path),
    ):
        # Should not raise
        check_lockfile_freshness(logger)

    logger.error.assert_not_called()


@pytest.mark.unit
def test_lockfile_check_exits_when_hash_differs(tmp_path):
    """SystemExit when the mounted uv.lock differs from the baked-in hash."""
    lock_content = b"new lockfile content"
    old_hash = hashlib.sha256(b"old lockfile content").hexdigest()

    lock_path = tmp_path / "uv.lock"
    hash_path = tmp_path / ".uv-lock-hash"
    lock_path.write_bytes(lock_content)
    hash_path.write_text(old_hash)

    logger = Mock()
    with (
        patch("app.tasks.lockfile_check.HASH_FILE", hash_path),
        patch("app.tasks.lockfile_check.LOCK_FILE", lock_path),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_lockfile_freshness(logger)

    assert "Rebuild" in str(exc_info.value)
    assert "uv.lock" in str(exc_info.value)
    logger.error.assert_called_once()
    assert logger.error.call_args.args[0] == "worker_lockfile_mismatch"


@pytest.mark.unit
def test_lockfile_check_skips_when_no_hash_file(tmp_path):
    """No error when the image predates the hash file (backward compatible)."""
    lock_path = tmp_path / "uv.lock"
    lock_path.write_bytes(b"some lockfile")

    logger = Mock()
    with (
        patch("app.tasks.lockfile_check.HASH_FILE", tmp_path / ".uv-lock-hash"),
        patch("app.tasks.lockfile_check.LOCK_FILE", lock_path),
    ):
        # Should not raise — hash file missing means older image
        check_lockfile_freshness(logger)

    logger.error.assert_not_called()


@pytest.mark.unit
def test_lockfile_check_runs_before_app_imports(tmp_path):
    """A stale image must fail with the rebuild hint, not a raw import error.

    The missing dependency surfaces as an ImportError from app.core.database
    (the asyncpg driver is loaded when the engine is built at import time).
    The check only helps if it runs before that import, so simulate the
    crash by blocking app.core.database and import the worker module fresh.
    """
    lock_path = tmp_path / "uv.lock"
    hash_path = tmp_path / ".uv-lock-hash"
    lock_path.write_bytes(b"new lockfile content")
    hash_path.write_text(hashlib.sha256(b"old lockfile content").hexdigest())

    script = textwrap.dedent(f"""
        import sys
        from pathlib import Path

        import app.tasks.lockfile_check as lockfile_check

        lockfile_check.HASH_FILE = Path({str(hash_path)!r})
        lockfile_check.LOCK_FILE = Path({str(lock_path)!r})

        class BlockDatabase:
            def find_spec(self, name, path, target=None):
                if name == "app.core.database":
                    raise ModuleNotFoundError("No module named 'asyncpg'")
                return None

        sys.meta_path.insert(0, BlockDatabase())
        import app.tasks.worker
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )

    assert result.returncode != 0
    assert "Rebuild" in result.stderr
    assert "asyncpg" not in result.stderr

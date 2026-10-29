"""Tests for r2_finalize_upload_job — first-sync of a newly uploaded image."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.tasks.r2_jobs import r2_finalize_upload_job


def _mock_session_cm(db_session):
    """Return an async context manager that yields db_session.

    r2_finalize_upload_job opens `async with get_async_session() as db:`, which
    bypasses the test's SAVEPOINT isolation.  This helper routes that call back
    to the test's db_session so the job sees the fixture data and its updates
    stay inside the same transaction.
    """
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def _write_local_variants(storage_path):
    """Create the fullsize + thumbs files the finalizer expects for fresh_image."""
    (storage_path / "fullsize").mkdir()
    (storage_path / "thumbs").mkdir()
    (storage_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
    (storage_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")


@pytest.fixture
async def fresh_image(db_session):
    img = Images(
        user_id=1,
        filename="2026-04-17-42",
        ext="jpg",
        status=ImageStatus.ACTIVE,
        medium=VariantStatus.NONE,
        large=VariantStatus.NONE,
        r2_location=R2Location.NONE,
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)
    return img


@pytest.mark.unit
class TestR2FinalizeUploadJob:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        """Route get_async_session() to the test's db_session."""
        with patch(
            "app.tasks.r2_jobs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    @pytest.fixture(autouse=True)
    def mock_purge(self):
        """Stub the Cloudflare purge — prod's .env holds a live zone token."""
        with patch("app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock) as mock:
            yield mock

    async def test_uploads_two_variants_and_flips_public(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        """Image with no medium/large: uploads fullsize+thumbs to public bucket."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        # Create the local files that the finalizer expects
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        assert mock_r2.upload_file.await_count == 2
        calls = {(c.kwargs["bucket"], c.kwargs["key"]) for c in mock_r2.upload_file.await_args_list}
        assert (settings.R2_PUBLIC_BUCKET, "fullsize/2026-04-17-42.jpg") in calls
        assert (settings.R2_PUBLIC_BUCKET, "thumbs/2026-04-17-42.webp") in calls

        # Flip happened
        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.PUBLIC

    async def test_protected_status_goes_to_private_bucket(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")

        fresh_image.status = ImageStatus.REVIEW
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        for c in mock_r2.upload_file.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PRIVATE_BUCKET

        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.PRIVATE

    async def test_uploads_medium_and_large_when_ready(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        for sub in ("fullsize", "thumbs", "medium", "large"):
            (tmp_path / sub).mkdir()
            suffix = "webp" if sub == "thumbs" else "jpg"
            (tmp_path / sub / f"2026-04-17-42.{suffix}").write_bytes(b"x")

        fresh_image.medium = VariantStatus.READY
        fresh_image.large = VariantStatus.READY
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        assert mock_r2.upload_file.await_count == 4

    async def test_retries_when_expected_variant_file_missing(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        """If medium=READY but file is missing on disk, the finalizer must retry."""
        from arq import Retry

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "medium").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"x")
        # medium dir exists but file does not

        fresh_image.medium = VariantStatus.READY
        await db_session.commit()

        mock_r2 = AsyncMock()
        with (
            patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2),
            pytest.raises(Retry),
        ):
            await r2_finalize_upload_job({"job_try": 1}, image_id=fresh_image.image_id)

        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.NONE  # no flip on retry

    async def test_retries_when_variant_file_is_empty(
        self, fresh_image, db_session, monkeypatch, tmp_path
    ):
        """A zero-byte variant is either mid-encode (img.save writes in place) or
        a write lost to a host crash. Neither is publishable: retry before
        uploading anything, so no variant lands in R2 while the row says NONE."""
        from arq import Retry

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        (tmp_path / "fullsize").mkdir()
        (tmp_path / "thumbs").mkdir()
        (tmp_path / "fullsize" / "2026-04-17-42.jpg").write_bytes(b"x")
        (tmp_path / "thumbs" / "2026-04-17-42.webp").write_bytes(b"")

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with (
            patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2),
            pytest.raises(Retry),
        ):
            await r2_finalize_upload_job({"job_try": 1}, image_id=fresh_image.image_id)

        mock_r2.upload_file.assert_not_awaited()
        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.NONE

    async def test_purges_cdn_after_public_finalize(
        self, fresh_image, monkeypatch, tmp_path, mock_purge
    ):
        """A GET for the CDN URL before the objects exist caches R2's 404 at
        the edge, and nothing else would evict it once the image goes public."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        _write_local_variants(tmp_path)

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        mock_purge.assert_awaited_once()
        assert set(mock_purge.await_args.args[0]) == {
            "https://cdn.example.com/fullsize/2026-04-17-42.jpg",
            "https://cdn.example.com/thumbs/2026-04-17-42.webp",
        }

    async def test_private_finalize_does_not_purge(
        self, fresh_image, db_session, monkeypatch, tmp_path, mock_purge
    ):
        """The CDN fronts only the public bucket, so a private finalize has
        nothing at the edge to refresh."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        _write_local_variants(tmp_path)
        fresh_image.status = ImageStatus.REVIEW
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        mock_purge.assert_not_awaited()

    async def test_purge_failure_does_not_undo_finalize(
        self, fresh_image, db_session, monkeypatch, tmp_path, mock_purge, caplog
    ):
        """The objects are uploaded and the flip committed; a purge failure is
        logged, not raised, so arq doesn't retry a finalize that already won."""
        import logging

        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
        _write_local_variants(tmp_path)
        mock_purge.side_effect = RuntimeError("cloudflare down")

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)
        with (
            patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2),
            caplog.at_level(logging.ERROR),
        ):
            result = await r2_finalize_upload_job({}, image_id=fresh_image.image_id)

        assert result == {"r2_location": int(R2Location.PUBLIC)}
        await db_session.refresh(fresh_image)
        assert fresh_image.r2_location == R2Location.PUBLIC
        log_messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "r2_cdn_purge_failed_post_finalize" in log_messages
        assert "cloudflare down" in log_messages

    async def test_no_op_when_r2_disabled(self, fresh_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)
        mock_r2.upload_file.assert_not_awaited()

    async def test_no_op_when_already_synced(self, fresh_image, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        fresh_image.r2_location = R2Location.PUBLIC
        await db_session.commit()
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_finalize_upload_job({}, image_id=fresh_image.image_id)
        mock_r2.upload_file.assert_not_awaited()

"""Tests for sync_image_status_job — bucket move on status change."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from app.tasks.r2_jobs import sync_image_status_job


def _mock_session_cm(db_session):
    """Route get_async_session() back to the test's SAVEPOINT-isolated session."""
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.fixture
async def synced_public_image(db_session):
    img = Images(
        user_id=1,
        filename="2026-04-17-7",
        ext="jpg",
        status=ImageStatus.ACTIVE,
        r2_location=R2Location.PUBLIC,
        medium=VariantStatus.READY,
        large=VariantStatus.NONE,
    )
    db_session.add(img)
    await db_session.commit()
    await db_session.refresh(img)
    return img


@pytest.mark.unit
class TestSyncImageStatusJob:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "app.tasks.r2_jobs.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_early_return_when_location_none(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        img = Images(
            user_id=1,
            filename="2026-04-17-8",
            ext="jpg",
            status=ImageStatus.REVIEW,
            r2_location=R2Location.NONE,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()
        mock_r2.delete_object.assert_not_awaited()

    async def test_no_op_when_public_to_public(self, synced_public_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.SPOILER,
            )
        mock_r2.copy_object.assert_not_awaited()

    async def test_public_to_protected_copies_deletes_and_purges(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        # Simulate post-commit state: status already changed to REVIEW,
        # but r2_location still PUBLIC (job hasn't moved objects yet).
        img = Images(
            user_id=1,
            filename="2026-04-17-7",
            ext="jpg",
            status=ImageStatus.REVIEW,
            r2_location=R2Location.PUBLIC,
            medium=VariantStatus.READY,
            large=VariantStatus.NONE,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)

        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )

        assert mock_r2.copy_object.await_count == 3
        assert mock_r2.delete_object.await_count == 3
        await db_session.refresh(img)
        assert img.r2_location == R2Location.PRIVATE
        mock_purge.assert_awaited_once()
        urls = mock_purge.await_args.args[0]
        assert all(u.startswith("https://cdn.example.com/") for u in urls)
        assert len(urls) == 3

    async def test_protected_to_public_copies_deletes_no_purge(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        img = Images(
            user_id=1,
            filename="2026-04-17-9",
            ext="jpg",
            status=ImageStatus.ACTIVE,
            r2_location=R2Location.PRIVATE,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.REVIEW,
                new_status=ImageStatus.ACTIVE,
            )

        assert mock_r2.copy_object.await_count == 2
        assert mock_r2.delete_object.await_count == 2
        mock_purge.assert_not_awaited()
        await db_session.refresh(img)
        assert img.r2_location == R2Location.PUBLIC

    async def test_skips_missing_source_objects(self, db_session, monkeypatch):
        """All source objects missing → no copies, no DB flip."""
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
        # Post-commit state: status=REVIEW, r2_location=PUBLIC.
        img = Images(
            user_id=1,
            filename="2026-04-17-missing",
            ext="jpg",
            status=ImageStatus.REVIEW,
            r2_location=R2Location.PUBLIC,
        )
        db_session.add(img)
        await db_session.commit()
        await db_session.refresh(img)

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)

        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ):
            result = await sync_image_status_job(
                {},
                image_id=img.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()
        mock_r2.delete_object.assert_not_awaited()
        assert result == {"skipped": "no_objects_moved"}
        await db_session.refresh(img)
        assert img.r2_location == R2Location.PUBLIC

    async def test_no_op_when_r2_disabled(self, synced_public_image, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await sync_image_status_job(
                {},
                image_id=synced_public_image.image_id,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_r2.copy_object.assert_not_awaited()

"""Integration test: split-existing moves protected images to private bucket."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images, VariantStatus
from scripts.r2_sync import split_existing


def _mock_session_cm(db_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.integration
class TestSplitExisting:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_moves_protected_images_only(
        self, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        db_session.add(
            Images(
                user_id=1,
                filename="2026-04-17-1",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        db_session.add(
            Images(
                user_id=1,
                filename="2026-04-17-2",
                ext="jpg",
                status=ImageStatus.REVIEW,
                r2_location=R2Location.PUBLIC,
            )
        )
        db_session.add(
            Images(
                user_id=1,
                filename="2026-04-17-3",
                ext="jpg",
                status=ImageStatus.REPOST,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)

        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await split_existing(dry_run=False)

        # Only image 2 (REVIEW) should be moved — 2 variants (fullsize + thumbs)
        assert mock_r2.copy_object.await_count == 2
        assert mock_r2.delete_object.await_count == 2

        copied_keys = {c.kwargs["key"] for c in mock_r2.copy_object.await_args_list}
        assert "fullsize/2026-04-17-2.jpg" in copied_keys
        assert "thumbs/2026-04-17-2.webp" in copied_keys
        assert "fullsize/2026-04-17-1.jpg" not in copied_keys
        assert "fullsize/2026-04-17-3.jpg" not in copied_keys

    async def test_dry_run_does_not_copy(
        self, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        db_session.add(
            Images(
                user_id=1,
                filename="2026-04-17-5",
                ext="jpg",
                status=ImageStatus.REVIEW,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=True)

        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await split_existing(dry_run=True)

        mock_r2.copy_object.assert_not_awaited()
        mock_r2.delete_object.assert_not_awaited()

    async def test_skips_missing_objects(
        self, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)

        db_session.add(
            Images(
                user_id=1,
                filename="2026-04-17-6",
                ext="jpg",
                status=ImageStatus.REVIEW,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        mock_r2 = AsyncMock()
        mock_r2.object_exists = AsyncMock(return_value=False)

        with patch("scripts.r2_sync.get_r2_storage", return_value=mock_r2):
            await split_existing(dry_run=False)

        mock_r2.copy_object.assert_not_awaited()

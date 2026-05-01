"""Integration test: backfill-locations fills r2_location based on current status."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.models.image import Images
from scripts.r2_sync import BulkBackfillDisallowedError, backfill_locations


def _mock_session_cm(db_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.integration
class TestBackfillLocations:
    @pytest.fixture(autouse=True)
    def _patch_get_session(self, db_session):
        with patch(
            "scripts.r2_sync.get_async_session",
            return_value=_mock_session_cm(db_session),
        ):
            yield

    async def test_respects_r2_allow_bulk_backfill_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)

        with pytest.raises(BulkBackfillDisallowedError):
            await backfill_locations()

    async def test_flips_public_and_private(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)

        db_session.add(
            Images(user_id=1, filename="a", ext="jpg", status=ImageStatus.ACTIVE)
        )
        db_session.add(
            Images(user_id=1, filename="b", ext="jpg", status=ImageStatus.REVIEW)
        )
        db_session.add(
            Images(user_id=1, filename="c", ext="jpg", status=ImageStatus.REPOST)
        )
        await db_session.commit()

        await backfill_locations(batch_size=2)

        result = await db_session.execute(select(Images))
        images = {img.filename: img for img in result.scalars()}
        assert images["a"].r2_location == R2Location.PUBLIC
        assert images["b"].r2_location == R2Location.PRIVATE
        assert images["c"].r2_location == R2Location.PUBLIC

    async def test_skips_already_set_rows(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)

        db_session.add(
            Images(
                user_id=1,
                filename="d",
                ext="jpg",
                status=ImageStatus.ACTIVE,
                r2_location=R2Location.PUBLIC,
            )
        )
        await db_session.commit()

        await backfill_locations()

        result = await db_session.execute(select(Images))
        img = result.scalar_one()
        assert img.r2_location == R2Location.PUBLIC

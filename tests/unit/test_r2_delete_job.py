"""Tests for r2_delete_image_job — full delete from R2 and CDN purge."""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.r2_constants import R2Location
from app.tasks.r2_jobs import r2_delete_image_job


@pytest.mark.unit
class TestR2DeleteImageJob:
    async def test_deletes_all_four_variants_from_public_and_purges(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")

        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PUBLIC),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs", "medium", "large"],
            )
        assert mock_r2.delete_object.await_count == 4
        for c in mock_r2.delete_object.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PUBLIC_BUCKET
        mock_purge.assert_awaited_once()
        assert len(mock_purge.await_args.args[0]) == 4

    async def test_deletes_from_private_no_purge(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2), patch(
            "app.tasks.r2_jobs.purge_cache_by_urls", new_callable=AsyncMock
        ) as mock_purge:
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PRIVATE),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        assert mock_r2.delete_object.await_count == 2
        for c in mock_r2.delete_object.await_args_list:
            assert c.kwargs["bucket"] == settings.R2_PRIVATE_BUCKET
        mock_purge.assert_not_awaited()

    async def test_no_op_when_location_none(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.NONE),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        mock_r2.delete_object.assert_not_awaited()

    async def test_no_op_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        mock_r2 = AsyncMock()
        with patch("app.tasks.r2_jobs.get_r2_storage", return_value=mock_r2):
            await r2_delete_image_job(
                {},
                image_id=42,
                r2_location=int(R2Location.PUBLIC),
                filename="2026-04-17-42",
                ext="jpg",
                variants=["fullsize", "thumbs"],
            )
        mock_r2.delete_object.assert_not_awaited()

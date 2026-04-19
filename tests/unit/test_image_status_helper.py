"""Tests for enqueue_r2_sync_on_status_change.

These guard the enqueue gating logic so none of the ~7 status-mutation call
sites can silently regress (they all go through this helper).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import ImageStatus, settings
from app.services.image_status import enqueue_r2_sync_on_status_change


@pytest.mark.unit
@pytest.mark.asyncio
class TestEnqueueR2SyncOnStatusChange:
    async def test_enqueues_when_status_changed_and_r2_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "app.services.image_status.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await enqueue_r2_sync_on_status_change(
                image_id=123,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_enqueue.assert_awaited_once_with(
            "sync_image_status_job",
            image_id=123,
            old_status=ImageStatus.ACTIVE,
            new_status=ImageStatus.REVIEW,
        )

    async def test_noop_when_status_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        with patch(
            "app.services.image_status.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await enqueue_r2_sync_on_status_change(
                image_id=123,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.ACTIVE,
            )
        mock_enqueue.assert_not_awaited()

    async def test_noop_when_r2_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with patch(
            "app.services.image_status.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await enqueue_r2_sync_on_status_change(
                image_id=123,
                old_status=ImageStatus.ACTIVE,
                new_status=ImageStatus.REVIEW,
            )
        mock_enqueue.assert_not_awaited()

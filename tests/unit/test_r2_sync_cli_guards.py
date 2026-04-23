"""Tests for r2_sync.py CLI guards (R2_ENABLED, R2_ALLOW_BULK_BACKFILL)."""

import pytest

from app.config import settings
from scripts.r2_sync import (
    BulkBackfillDisallowedError,
    R2DisabledError,
    require_bulk_backfill,
    require_r2_enabled,
)


@pytest.mark.unit
class TestRequireR2Enabled:
    def test_passes_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        require_r2_enabled()

    def test_raises_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        with pytest.raises(R2DisabledError):
            require_r2_enabled()


@pytest.mark.unit
class TestRequireBulkBackfill:
    def test_passes_when_allowed(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", True)
        require_bulk_backfill()

    def test_raises_when_disallowed(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ALLOW_BULK_BACKFILL", False)
        with pytest.raises(BulkBackfillDisallowedError):
            require_bulk_backfill()

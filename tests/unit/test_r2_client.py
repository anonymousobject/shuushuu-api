"""Tests for the R2 client singleton accessor."""

import pytest

from app.config import settings
from app.core.r2_client import get_r2_storage, reset_r2_storage
from app.services.r2_storage import DummyR2Storage, R2Storage


@pytest.fixture(autouse=True)
def _reset():
    reset_r2_storage()
    yield
    reset_r2_storage()


@pytest.mark.unit
class TestGetR2Storage:
    def test_returns_dummy_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        storage = get_r2_storage()
        assert isinstance(storage, DummyR2Storage)

    def test_returns_real_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "ak")
        monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "sk")
        monkeypatch.setattr(
            settings, "R2_ENDPOINT", "https://example.r2.cloudflarestorage.com"
        )
        storage = get_r2_storage()
        assert isinstance(storage, R2Storage)

    def test_singleton_stable_within_mode(self, monkeypatch):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        assert get_r2_storage() is get_r2_storage()

    async def test_dummy_methods_raise(self):
        storage = DummyR2Storage()
        with pytest.raises(RuntimeError, match="R2 is disabled"):
            await storage.object_exists(bucket="b", key="k")

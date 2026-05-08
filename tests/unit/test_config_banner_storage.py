"""Tests for BANNER_STORAGE_PATH setting derivation."""


def test_banner_storage_path_default_derives_from_storage_path(monkeypatch):
    from app.config import Settings

    s = Settings(STORAGE_PATH="/tmp/test", BANNER_STORAGE_PATH="")
    assert s.BANNER_STORAGE_PATH == "/tmp/test/banners"


def test_banner_storage_path_explicit_value_preserved():
    from app.config import Settings

    s = Settings(STORAGE_PATH="/tmp/test", BANNER_STORAGE_PATH="/elsewhere")
    assert s.BANNER_STORAGE_PATH == "/elsewhere"

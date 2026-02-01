"""Tests for banner-related configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_banner_settings_defaults() -> None:
    settings = Settings()

    assert settings.BANNER_BASE_URL == f"{settings.IMAGE_BASE_URL}/images/banners"
    assert settings.BANNER_CACHE_TTL == 600
    assert settings.BANNER_CACHE_TTL_JITTER == 300


def test_banner_settings_reject_negative_ttl_values() -> None:
    with pytest.raises(ValidationError):
        Settings(BANNER_CACHE_TTL=-1)

    with pytest.raises(ValidationError):
        Settings(BANNER_CACHE_TTL_JITTER=-1)

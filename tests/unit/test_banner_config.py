"""Tests for banner-related configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_banner_settings_exist_and_valid() -> None:
    """Test banner settings exist and have valid values."""
    settings = Settings()

    # BANNER_BASE_URL should be derived from IMAGE_BASE_URL
    assert settings.BANNER_BASE_URL == f"{settings.IMAGE_BASE_URL}/images/banners"

    # TTL values should be non-negative
    assert settings.BANNER_CACHE_TTL >= 0
    assert settings.BANNER_CACHE_TTL_JITTER >= 0


def test_banner_settings_reject_negative_ttl_values() -> None:
    with pytest.raises(ValidationError):
        Settings(BANNER_CACHE_TTL=-1)

    with pytest.raises(ValidationError):
        Settings(BANNER_CACHE_TTL_JITTER=-1)

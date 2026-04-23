"""Tests for R2 config validation."""

import pytest

from app.config import Settings


@pytest.mark.unit
class TestR2ConfigValidation:
    """R2_ENABLED requires R2_* credentials; CLOUDFLARE_* are optional."""

    def test_r2_disabled_requires_no_credentials(self):
        """When R2_ENABLED=false, empty R2/Cloudflare fields are fine."""
        s = Settings(_env_file=None, R2_ENABLED=False)
        assert s.R2_ENABLED is False

    def test_r2_enabled_requires_all_credentials(self):
        """When R2_ENABLED=true, missing credentials fail validation."""
        with pytest.raises(ValueError, match="R2_ACCESS_KEY_ID"):
            Settings(
                _env_file=None,
                R2_ENABLED=True,
                R2_ACCESS_KEY_ID="",
                R2_SECRET_ACCESS_KEY="sk",
                R2_ENDPOINT="https://example.r2.cloudflarestorage.com",
                R2_PUBLIC_CDN_URL="https://cdn.example.com",
                CLOUDFLARE_API_TOKEN="tok",
                CLOUDFLARE_ZONE_ID="zone",
            )

    def test_r2_enabled_with_all_credentials_passes(self):
        """Full credentials pass validation."""
        s = Settings(
            _env_file=None,
            R2_ENABLED=True,
            R2_ACCESS_KEY_ID="ak",
            R2_SECRET_ACCESS_KEY="sk",
            R2_ENDPOINT="https://example.r2.cloudflarestorage.com",
            R2_PUBLIC_CDN_URL="https://cdn.example.com",
            CLOUDFLARE_API_TOKEN="tok",
            CLOUDFLARE_ZONE_ID="zone",
        )
        assert s.R2_ENABLED is True

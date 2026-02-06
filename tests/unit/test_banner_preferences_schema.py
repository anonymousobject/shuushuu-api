"""Tests for banner preference schemas."""

import pytest
from pydantic import ValidationError

from app.models.misc import BannerSize, BannerTheme
from app.schemas.banner import (
    BannerPinResponse,
    BannerPreferencesResponse,
    PinRequest,
    PreferenceUpdateRequest,
)


class TestPreferenceUpdateRequest:
    def test_valid_size(self):
        req = PreferenceUpdateRequest(preferred_size=BannerSize.large)
        assert req.preferred_size == BannerSize.large

    def test_rejects_invalid_size(self):
        with pytest.raises(ValidationError):
            PreferenceUpdateRequest(preferred_size="huge")


class TestPinRequest:
    def test_valid_banner_id(self):
        req = PinRequest(banner_id=42)
        assert req.banner_id == 42

    def test_rejects_missing_banner_id(self):
        with pytest.raises(ValidationError):
            PinRequest()


class TestBannerPinResponse:
    def test_fields(self):
        pin = BannerPinResponse(
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner=None,
        )
        assert pin.size == BannerSize.small
        assert pin.theme == BannerTheme.dark
        assert pin.banner is None


class TestBannerPreferencesResponse:
    def test_defaults(self):
        resp = BannerPreferencesResponse(
            preferred_size=BannerSize.small,
            pins=[],
        )
        assert resp.preferred_size == BannerSize.small
        assert resp.pins == []

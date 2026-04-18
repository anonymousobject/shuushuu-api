"""Tests for status-aware image URL generation.

Matrix: 7 statuses × 3 r2_location values × 2 R2_ENABLED values. A direct-CDN
URL must be emitted only when R2_ENABLED=true AND status is public AND
r2_location=PUBLIC. Every other combination falls back to the /images/ path.
"""

from datetime import datetime

import pytest

from app.config import ImageStatus, settings
from app.core.r2_constants import R2Location
from app.schemas.image import ImageResponse

ALL_STATUSES = [
    ImageStatus.REVIEW,
    ImageStatus.LOW_QUALITY,
    ImageStatus.INAPPROPRIATE,
    ImageStatus.REPOST,
    ImageStatus.OTHER,
    ImageStatus.ACTIVE,
    ImageStatus.SPOILER,
]
PUBLIC_STATUSES = [ImageStatus.ACTIVE, ImageStatus.SPOILER, ImageStatus.REPOST]


def _make_image(status: int, r2_location: int, medium: int = 0, large: int = 0) -> ImageResponse:
    return ImageResponse(
        image_id=1,
        user_id=1,
        filename="2026-04-17-1",
        ext="jpg",
        status=status,
        r2_location=r2_location,
        date_added=datetime(2026, 4, 17),
        locked=0,
        posts=0,
        favorites=0,
        bayesian_rating=0.0,
        num_ratings=0,
        medium=medium,
        large=large,
    )


@pytest.fixture
def r2_on(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example.com")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://localhost:3000")


@pytest.fixture
def r2_off(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://localhost:3000")


@pytest.mark.unit
class TestUrlGenerationR2Off:
    """With R2 disabled, everything goes through /images/ — no exceptions."""

    @pytest.mark.parametrize("status", ALL_STATUSES)
    @pytest.mark.parametrize(
        "location", [R2Location.NONE, R2Location.PUBLIC, R2Location.PRIVATE]
    )
    def test_url_fallback(self, r2_off, status, location):
        img = _make_image(status=status, r2_location=location)
        assert img.url == "http://localhost:3000/images/2026-04-17-1.jpg"
        assert img.thumbnail_url == "http://localhost:3000/thumbs/2026-04-17-1.webp"


@pytest.mark.unit
class TestUrlGenerationR2On:
    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_cdn_direct_for_public_status_public_location(self, r2_on, status):
        img = _make_image(status=status, r2_location=R2Location.PUBLIC)
        assert img.url == "https://cdn.example.com/fullsize/2026-04-17-1.jpg"
        assert img.thumbnail_url == "https://cdn.example.com/thumbs/2026-04-17-1.webp"

    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_fallback_when_location_none(self, r2_on, status):
        img = _make_image(status=status, r2_location=R2Location.NONE)
        assert img.url.startswith("http://localhost:3000/images/")
        assert img.thumbnail_url.startswith("http://localhost:3000/thumbs/")

    @pytest.mark.parametrize("status", PUBLIC_STATUSES)
    def test_fallback_when_location_private(self, r2_on, status):
        """Public status with PRIVATE location means a transition is in flight."""
        img = _make_image(status=status, r2_location=R2Location.PRIVATE)
        assert img.url.startswith("http://localhost:3000/images/")
        assert img.thumbnail_url.startswith("http://localhost:3000/thumbs/")

    @pytest.mark.parametrize(
        "status",
        [
            ImageStatus.REVIEW,
            ImageStatus.LOW_QUALITY,
            ImageStatus.INAPPROPRIATE,
            ImageStatus.OTHER,
        ],
    )
    @pytest.mark.parametrize(
        "location", [R2Location.NONE, R2Location.PUBLIC, R2Location.PRIVATE]
    )
    def test_protected_never_direct_cdn(self, r2_on, status, location):
        """Protected statuses must never emit a CDN URL, regardless of location."""
        img = _make_image(status=status, r2_location=location)
        assert "cdn.example.com" not in img.url
        assert img.url.startswith("http://localhost:3000/images/")


@pytest.mark.unit
class TestMediumLargeUrls:
    def test_medium_none_returns_none(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC, medium=0, large=0
        )
        assert img.medium_url is None
        assert img.large_url is None

    def test_medium_ready_uses_cdn(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.PUBLIC, medium=1, large=1
        )
        assert img.medium_url == "https://cdn.example.com/medium/2026-04-17-1.jpg"
        assert img.large_url == "https://cdn.example.com/large/2026-04-17-1.jpg"

    def test_medium_ready_fallback_when_none_location(self, r2_on):
        img = _make_image(
            status=ImageStatus.ACTIVE, r2_location=R2Location.NONE, medium=1, large=1
        )
        assert img.medium_url == "http://localhost:3000/medium/2026-04-17-1.jpg"
        assert img.large_url == "http://localhost:3000/large/2026-04-17-1.jpg"

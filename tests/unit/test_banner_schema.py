"""Tests for Banner schemas."""

import pytest
from pydantic import ValidationError

from app.config import settings
from app.models.misc import BannerSize


def test_full_banner_response() -> None:
    from app.schemas.banner import BannerResponse

    response = BannerResponse(
        banner_id=1,
        name="test_banner",
        author="artist",
        size=BannerSize.medium,
        full_image="test.png",
        left_image=None,
        middle_image=None,
        right_image=None,
        supports_dark=True,
        supports_light=True,
    )

    assert response.is_full is True
    assert response.full_image_url == f"{settings.BANNER_BASE_URL}/test.png"
    assert response.left_image_url is None


def test_three_part_banner_response() -> None:
    from app.schemas.banner import BannerResponse

    response = BannerResponse(
        banner_id=2,
        name="three_part",
        author=None,
        size=BannerSize.large,
        full_image=None,
        left_image="left.png",
        middle_image="middle.png",
        right_image="right.png",
        supports_dark=True,
        supports_light=False,
    )

    assert response.is_full is False
    assert response.full_image_url is None
    assert response.left_image_url == f"{settings.BANNER_BASE_URL}/left.png"
    assert response.middle_image_url == f"{settings.BANNER_BASE_URL}/middle.png"
    assert response.right_image_url == f"{settings.BANNER_BASE_URL}/right.png"


def test_banner_response_rejects_mixed_layout() -> None:
    from app.schemas.banner import BannerResponse

    with pytest.raises(ValidationError):
        BannerResponse(
            banner_id=3,
            name="mixed",
            author=None,
            size=BannerSize.medium,
            full_image="full.png",
            left_image="left.png",
            middle_image="middle.png",
            right_image="right.png",
            supports_dark=True,
            supports_light=True,
        )


def test_banner_response_requires_some_layout() -> None:
    from app.schemas.banner import BannerResponse

    with pytest.raises(ValidationError):
        BannerResponse(
            banner_id=4,
            name="empty",
            author=None,
            size=BannerSize.medium,
            full_image=None,
            left_image=None,
            middle_image=None,
            right_image=None,
            supports_dark=True,
            supports_light=True,
        )


def test_banner_response_requires_all_three_parts() -> None:
    from app.schemas.banner import BannerResponse

    with pytest.raises(ValidationError):
        BannerResponse(
            banner_id=5,
            name="partial",
            author=None,
            size=BannerSize.medium,
            full_image=None,
            left_image="left.png",
            middle_image=None,
            right_image="right.png",
            supports_dark=True,
            supports_light=True,
        )

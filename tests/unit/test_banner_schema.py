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
        size=BannerSize.small,
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
            size=BannerSize.small,
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
            size=BannerSize.small,
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
            size=BannerSize.small,
            full_image=None,
            left_image="left.png",
            middle_image=None,
            right_image="right.png",
            supports_dark=True,
            supports_light=True,
        )


def test_banner_response_uses_cdn_when_in_r2_and_enabled(monkeypatch) -> None:
    from app.schemas.banner import BannerResponse

    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1,
        name="t",
        author=None,
        size=BannerSize.small,
        supports_dark=True,
        supports_light=True,
        full_image="eva/full.jpg",
        left_image=None,
        middle_image=None,
        right_image=None,
        in_r2=True,
    )
    assert resp.full_image_url == "https://cdn.test/banners/eva/full.jpg"


def test_banner_response_falls_back_when_bit_false(monkeypatch) -> None:
    from app.schemas.banner import BannerResponse

    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1,
        name="t",
        author=None,
        size=BannerSize.small,
        supports_dark=True,
        supports_light=True,
        full_image="eva/full.jpg",
        left_image=None,
        middle_image=None,
        right_image=None,
        in_r2=False,
    )
    assert resp.full_image_url == "http://local.test/banners/eva/full.jpg"


def test_banner_response_three_part_uses_cdn(monkeypatch) -> None:
    from app.schemas.banner import BannerResponse

    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "BANNER_BASE_URL", "http://local.test/banners")

    resp = BannerResponse(
        banner_id=1,
        name="t",
        author=None,
        size=BannerSize.large,
        supports_dark=True,
        supports_light=True,
        full_image=None,
        left_image="hw/l.png",
        middle_image="hw/m.png",
        right_image="hw/r.png",
        in_r2=True,
    )
    assert resp.left_image_url == "https://cdn.test/banners/hw/l.png"
    assert resp.middle_image_url == "https://cdn.test/banners/hw/m.png"
    assert resp.right_image_url == "https://cdn.test/banners/hw/r.png"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("eva/full.jpg", "image/jpeg"),
        ("foo.JPEG", "image/jpeg"),
        ("hw/l.png", "image/png"),
        ("anim.gif", "image/gif"),
        ("modern.webp", "image/webp"),
    ],
)
def test_banner_content_type_known_extensions(path, expected):
    from app.services.banner import banner_content_type

    assert banner_content_type(path) == expected


def test_banner_content_type_raises_on_unknown_extension():
    from app.services.banner import banner_content_type

    with pytest.raises(ValueError, match="No Content-Type mapping for banner path"):
        banner_content_type("foo.bmp")


def test_banner_content_type_raises_on_missing_extension():
    from app.services.banner import banner_content_type

    with pytest.raises(ValueError, match="No Content-Type mapping for banner path"):
        banner_content_type("noextension")


def test_banner_response_round_trip_preserves_in_r2() -> None:
    """``in_r2`` MUST survive a model_dump_json -> model_validate_json cycle.

    Banner responses are cached in redis via ``model_dump_json()`` and read back
    via ``model_validate_json()``. If ``in_r2`` were excluded from serialization
    (as it once was), every cache hit would deserialize with the default
    ``False`` and silently fall back to the local-FS URL even when the DB row
    has ``in_r2=True``.
    """

    from app.schemas.banner import BannerResponse

    resp = BannerResponse(
        banner_id=1,
        name="t",
        author=None,
        size=BannerSize.small,
        supports_dark=True,
        supports_light=True,
        full_image="eva/full.png",
        left_image=None,
        middle_image=None,
        right_image=None,
        in_r2=True,
    )

    round_tripped = BannerResponse.model_validate_json(resp.model_dump_json())

    assert round_tripped.in_r2 is True, (
        "in_r2 was lost across the cache serialization round trip"
    )

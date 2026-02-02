"""Tests for Banner model."""

from app.models.misc import BannerSize, Banners


class TestBannerSize:
    """Tests for BannerSize enum."""

    def test_banner_size_values(self) -> None:
        assert BannerSize.small.value == "small"
        assert BannerSize.medium.value == "medium"
        assert BannerSize.large.value == "large"

    def test_banner_size_is_string_enum(self) -> None:
        assert isinstance(BannerSize.small.value, str)


class TestBannerModel:
    """Tests for Banners model."""

    def test_banner_has_required_fields(self) -> None:
        banner = Banners(
            name="test_banner",
            full_image="test.png",
        )
        assert banner.name == "test_banner"
        assert banner.size == BannerSize.medium
        assert banner.supports_dark is True
        assert banner.supports_light is True
        assert banner.active is True

    def test_banner_three_part_fields(self) -> None:
        banner = Banners(
            name="three_part",
            left_image="left.png",
            middle_image="middle.png",
            right_image="right.png",
        )
        assert banner.left_image == "left.png"
        assert banner.middle_image == "middle.png"
        assert banner.right_image == "right.png"
        assert banner.full_image is None

    def test_banner_allows_invalid_layout_in_db_model(self) -> None:
        """DB model should allow rows; validation happens in schema/service."""
        banner = Banners(name="invalid", left_image="left.png")
        assert banner.left_image == "left.png"

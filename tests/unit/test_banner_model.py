"""Tests for Banner model."""

from app.models.misc import BannerSize, BannerTheme, Banners, UserBannerPins, UserBannerPreferences


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


class TestBannerThemeEnum:
    def test_values(self):
        assert BannerTheme.dark == "dark"
        assert BannerTheme.light == "light"

    def test_has_exactly_two_values(self):
        assert len(BannerTheme) == 2


class TestUserBannerPreferencesModel:
    def test_default_preferred_size(self):
        prefs = UserBannerPreferences(user_id=1)
        assert prefs.preferred_size == BannerSize.small

    def test_custom_preferred_size(self):
        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        assert prefs.preferred_size == BannerSize.large

    def test_table_name(self):
        assert UserBannerPreferences.__tablename__ == "user_banner_preferences"


class TestUserBannerPinsModel:
    def test_fields(self):
        pin = UserBannerPins(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=10,
        )
        assert pin.user_id == 1
        assert pin.size == BannerSize.small
        assert pin.theme == BannerTheme.dark
        assert pin.banner_id == 10

    def test_table_name(self):
        assert UserBannerPins.__tablename__ == "user_banner_pins"

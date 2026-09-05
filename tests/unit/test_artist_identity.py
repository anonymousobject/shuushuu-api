"""Tests for artist identity URL/query parsing."""

import pytest

from app.services.artist_identity import (
    SITE_PIXIV,
    ArtistIdentity,
    canonical_profile_url,
    parse_identity_query,
    parse_identity_url,
    site_display_name,
)


class TestParseIdentityUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.pixiv.net/users/21412050",
            "https://pixiv.net/users/21412050",
            "http://www.pixiv.net/users/21412050",
            "https://www.pixiv.net/en/users/21412050",
            "https://touch.pixiv.net/users/21412050",
            "https://www.pixiv.net/member.php?id=21412050",
            "https://www.pixiv.net/member_illust.php?mode=medium&id=21412050",
            "  https://www.pixiv.net/users/21412050  ",
        ],
    )
    def test_parses_pixiv_profile_urls(self, url):
        assert parse_identity_url(url) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")

    @pytest.mark.parametrize(
        "url",
        [
            "https://twitter.com/someone",  # no parser registered in v1
            "https://www.pixiv.net/artworks/12345",  # artwork, not a profile
            "https://example.com/users/123",
            "not a url",
            "",
        ],
    )
    def test_rejects_non_identity_urls(self, url):
        assert parse_identity_url(url) is None

    def test_trailing_path_segments_allowed(self):
        url = "https://www.pixiv.net/users/21412050/illustrations"
        assert parse_identity_url(url) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")


class TestParseIdentityQuery:
    def test_bare_digits_are_a_pixiv_id(self):
        assert parse_identity_query("21412050") == ArtistIdentity(
            site=SITE_PIXIV, external_id="21412050"
        )

    @pytest.mark.parametrize("q", ["pixiv 21412050", "Pixiv 21412050", "pixiv:21412050"])
    def test_pixiv_prefixed_id(self, q):
        assert parse_identity_query(q) == ArtistIdentity(site=SITE_PIXIV, external_id="21412050")

    def test_full_url_query(self):
        assert parse_identity_query("https://www.pixiv.net/users/21412050") == ArtistIdentity(
            site=SITE_PIXIV, external_id="21412050"
        )

    @pytest.mark.parametrize("q", ["sakura", "pixiv", "21412050 extra words", ""])
    def test_non_identity_queries_return_none(self, q):
        assert parse_identity_query(q) is None


class TestCanonicalProfileUrl:
    def test_pixiv(self):
        identity = ArtistIdentity(site=SITE_PIXIV, external_id="21412050")
        assert canonical_profile_url(identity) == "https://www.pixiv.net/users/21412050"

    def test_non_pixiv_site_raises(self):
        """No other site's URL scheme has been registered yet — minting a
        pixiv URL for it would silently write a wrong link. This guard is a
        tripwire for the day a second parser is added without updating this
        function too."""
        identity = ArtistIdentity(site="twitter", external_id="21412050")
        with pytest.raises(ValueError, match="twitter"):
            canonical_profile_url(identity)


class TestSiteDisplayName:
    def test_pixiv_is_capitalized(self):
        """Mods capitalize 'Pixiv' everywhere; the stored site column stays
        lowercase, but any label built for display must use this form."""
        assert site_display_name(SITE_PIXIV) == "Pixiv"

    def test_unknown_site_falls_back_to_raw_string(self):
        """No display mapping registered yet -- fall back rather than raise,
        since this is a display concern, not a validation one."""
        assert site_display_name("twitter") == "twitter"

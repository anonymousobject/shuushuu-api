"""Tests for the dev-only fixture resolver used by e2e."""

import httpx

from app.services.url_import.fixture import FixtureResolver
from app.services.url_import.registry import get_resolver


class TestFixtureResolver:
    async def test_single_post(self):
        async with httpx.AsyncClient() as client:  # never used; resolve makes no calls
            post = await FixtureResolver().resolve(
                "https://urlimport-fixture.local/post/single", client
            )
        assert len(post.images) == 1
        assert post.canonical_url == "https://urlimport-fixture.local/post/single"
        assert post.title == "Fixture post"
        assert post.artist_name == "Fixture Artist"
        assert "/api/v1/images/url-import-fixture/" in post.images[0].full_url

    async def test_multi_post_has_three_images(self):
        async with httpx.AsyncClient() as client:
            post = await FixtureResolver().resolve(
                "https://urlimport-fixture.local/post/multi", client
            )
        assert len(post.images) == 3
        assert len({img.full_url for img in post.images}) == 3

    async def test_webp_kind_has_one_webp_image(self):
        async with httpx.AsyncClient() as client:
            post = await FixtureResolver().resolve(
                "https://urlimport-fixture.local/post/webp", client
            )
        assert len(post.images) == 1
        assert post.images[0].full_url.endswith(".webp")

    def test_registered_in_development(self):
        # tests run with ENVIRONMENT=development, so the resolver must be registered
        assert isinstance(
            get_resolver("https://urlimport-fixture.local/post/single"), FixtureResolver
        )

    def test_does_not_match_other_hosts(self):
        assert not FixtureResolver().match("https://urlimport-fixture.evil/post/single")

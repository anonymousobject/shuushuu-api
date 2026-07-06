"""Tests for the fxtwitter-backed twitter/X resolver."""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, UpstreamError
from app.services.url_import.registry import get_resolver
from app.services.url_import.twitter import TwitterResolver

URL = "https://x.com/someartist/status/1234567890"


def _client(json_body, status=200):
    def handler(request):
        assert request.url.host == "api.fxtwitter.com"
        return httpx.Response(status, json=json_body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _tweet_body(photos):
    return {
        "code": 200,
        "tweet": {
            "url": "https://twitter.com/someartist/status/1234567890",
            "author": {"name": "Some Artist", "screen_name": "someartist"},
            "media": {"photos": photos},
        },
    }


class TestMatch:
    def test_matches_twitter_and_x(self):
        resolver = TwitterResolver()
        assert resolver.match(URL)
        assert resolver.match("https://twitter.com/a_b/status/99")
        assert not resolver.match("https://x.com/someartist")
        assert isinstance(get_resolver(URL), TwitterResolver)


class TestResolve:
    async def test_photos_get_orig_variant(self):
        photos = [
            {"url": "https://pbs.twimg.com/media/AAA.jpg", "width": 1200, "height": 900},
            {"url": "https://pbs.twimg.com/media/BBB.jpg", "width": 800, "height": 600},
        ]
        async with _client(_tweet_body(photos)) as client:
            post = await TwitterResolver().resolve(URL, client)
        assert post.images[0].full_url == "https://pbs.twimg.com/media/AAA.jpg?name=orig"
        assert post.images[0].thumb_url == "https://pbs.twimg.com/media/AAA.jpg?name=small"
        assert len(post.images) == 2
        assert post.artist_name == "Some Artist"
        assert post.artist_id == "someartist"
        assert post.canonical_url == "https://twitter.com/someartist/status/1234567890"

    async def test_no_photos_raises_not_found(self):
        async with _client(_tweet_body([])) as client:
            with pytest.raises(PostNotFoundError):
                await TwitterResolver().resolve(URL, client)

    async def test_fxtwitter_404_code(self):
        async with _client({"code": 404, "message": "NOT_FOUND"}) as client:
            with pytest.raises(PostNotFoundError):
                await TwitterResolver().resolve(URL, client)

    async def test_fxtwitter_500_code(self):
        async with _client({"code": 500, "message": "API_FAIL"}) as client:
            with pytest.raises(UpstreamError):
                await TwitterResolver().resolve(URL, client)

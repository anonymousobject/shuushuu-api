"""Tests for the fxtwitter-backed twitter/X resolver."""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, UpstreamError
from app.services.url_import.registry import get_resolver
from app.services.url_import.twitter import TwitterResolver, _variant

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


class TestVariant:
    def test_ampersand_when_query_already_present(self):
        assert (
            _variant("https://pbs.twimg.com/media/AAA.jpg?format=jpg", "orig")
            == "https://pbs.twimg.com/media/AAA.jpg?format=jpg&name=orig"
        )

    def test_replaces_existing_name_param_instead_of_duplicating(self):
        # fxtwitter's real photos[].url already includes ?name=orig (confirmed
        # live 2026-07-06 against api.fxtwitter.com) -- appending blindly
        # produced ...jpg?name=orig&name=orig, which pbs.twimg.com 404s on.
        assert (
            _variant("https://pbs.twimg.com/media/AAA.jpg?name=orig", "small")
            == "https://pbs.twimg.com/media/AAA.jpg?name=small"
        )


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

    async def test_media_key_absent_raises_not_found(self):
        body = _tweet_body([])
        del body["tweet"]["media"]
        async with _client(body) as client:
            with pytest.raises(PostNotFoundError):
                await TwitterResolver().resolve(URL, client)

    async def test_author_absent_resolves_with_none_artist(self):
        body = _tweet_body(
            [{"url": "https://pbs.twimg.com/media/AAA.jpg", "width": 1200, "height": 900}]
        )
        del body["tweet"]["author"]
        async with _client(body) as client:
            post = await TwitterResolver().resolve(URL, client)
        assert post.artist_name is None
        assert post.artist_id is None

    async def test_tweet_url_absent_canonical_falls_back(self):
        body = _tweet_body(
            [{"url": "https://pbs.twimg.com/media/AAA.jpg", "width": 1200, "height": 900}]
        )
        del body["tweet"]["url"]
        async with _client(body) as client:
            post = await TwitterResolver().resolve(URL, client)
        assert post.canonical_url == "https://twitter.com/someartist/status/1234567890"

    async def test_missing_tweet_key_raises_upstream_error(self):
        async with _client({"code": 200}) as client:
            with pytest.raises(UpstreamError):
                await TwitterResolver().resolve(URL, client)

    async def test_fxtwitter_404_code(self):
        # fetch_json's HTTP-status mapping (base.py) already turns a real HTTP 404
        # into PostNotFoundError before this resolver inspects the JSON body at
        # all; fxtwitter mirrors its JSON `code` to the HTTP status, so this test
        # exercises that first layer. The `code == 404` check in resolve() below
        # is a second layer that only matters if the JSON code and HTTP status
        # ever disagree.
        async with _client({"code": 404, "message": "NOT_FOUND"}, status=404) as client:
            with pytest.raises(PostNotFoundError):
                await TwitterResolver().resolve(URL, client)

    async def test_fxtwitter_500_code(self):
        async with _client({"code": 500, "message": "API_FAIL"}) as client:
            with pytest.raises(UpstreamError):
                await TwitterResolver().resolve(URL, client)

    async def test_off_host_photo_url_rejects_whole_post(self):
        photos = [{"url": "https://evil.example/a.jpg", "width": 1200, "height": 900}]
        async with _client(_tweet_body(photos)) as client:
            with pytest.raises(UpstreamError):
                await TwitterResolver().resolve(URL, client)

    async def test_one_off_host_photo_rejects_whole_post_even_if_others_are_pinned(self):
        photos = [
            {"url": "https://pbs.twimg.com/media/AAA.jpg", "width": 1200, "height": 900},
            {"url": "https://evil.example/b.jpg", "width": 800, "height": 600},
        ]
        async with _client(_tweet_body(photos)) as client:
            with pytest.raises(UpstreamError):
                await TwitterResolver().resolve(URL, client)

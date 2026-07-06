"""Tests for the danbooru / gelbooru / yande.re resolvers."""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, RestrictedContentError
from app.services.url_import.danbooru import DanbooruResolver
from app.services.url_import.gelbooru import GelbooruResolver
from app.services.url_import.moebooru import MoebooruResolver
from app.services.url_import.registry import get_resolver


def _client(json_body, status=200):
    def handler(request):
        return httpx.Response(status, json=json_body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestDanbooru:
    URL = "https://danbooru.donmai.us/posts/123456"
    POST = {
        "id": 123456,
        "file_url": "https://cdn.donmai.us/original/ab/cd/abcd.jpg",
        "preview_file_url": "https://cdn.donmai.us/preview/ab/cd/abcd.jpg",
        "image_width": 800,
        "image_height": 600,
        "tag_string_artist": "some_artist",
        "source": "https://www.pixiv.net/artworks/999",
    }

    def test_match_and_registration(self):
        assert DanbooruResolver().match(self.URL)
        assert not DanbooruResolver().match("https://danbooru.donmai.us/pools/1")
        assert isinstance(get_resolver(self.URL), DanbooruResolver)

    async def test_resolve_prefers_upstream_source_as_canonical(self):
        async with _client(self.POST) as client:
            post = await DanbooruResolver().resolve(self.URL, client)
        assert post.canonical_url == "https://www.pixiv.net/artworks/999"
        assert post.images[0].full_url == self.POST["file_url"]
        assert post.images[0].width == 800
        assert post.artist_name == "some artist"

    async def test_non_url_source_falls_back_to_post_url(self):
        async with _client({**self.POST, "source": "self-drawn"}) as client:
            post = await DanbooruResolver().resolve(self.URL, client)
        assert post.canonical_url == self.URL

    async def test_missing_file_url_is_restricted(self):
        body = {k: v for k, v in self.POST.items() if k != "file_url"}
        async with _client(body) as client:
            with pytest.raises(RestrictedContentError):
                await DanbooruResolver().resolve(self.URL, client)


class TestGelbooru:
    URL = "https://gelbooru.com/index.php?page=post&s=view&id=987"
    BODY = {
        "post": [
            {
                "file_url": "https://img3.gelbooru.com/images/ab/cd/abcd.jpg",
                "preview_url": "https://img3.gelbooru.com/thumbnails/ab/cd/thumbnail_abcd.jpg",
                "width": 1000,
                "height": 1500,
                "source": "",
            }
        ]
    }

    def test_match_requires_view_params(self):
        resolver = GelbooruResolver()
        assert resolver.match(self.URL)
        assert not resolver.match("https://gelbooru.com/index.php?page=post&s=list&tags=x")
        assert isinstance(get_resolver(self.URL), GelbooruResolver)

    async def test_resolve(self):
        async with _client(self.BODY) as client:
            post = await GelbooruResolver().resolve(self.URL, client)
        assert post.canonical_url == self.URL
        assert post.images[0].full_url == self.BODY["post"][0]["file_url"]

    async def test_empty_post_list_not_found(self):
        async with _client({"post": []}) as client:
            with pytest.raises(PostNotFoundError):
                await GelbooruResolver().resolve(self.URL, client)


class TestMoebooru:
    URL = "https://yande.re/post/show/555"
    BODY = [
        {
            "id": 555,
            "file_url": "https://files.yande.re/image/abcd/yande.re%20555.jpg",
            "preview_url": "https://assets.yande.re/data/preview/ab/cd/abcd.jpg",
            "width": 2000,
            "height": 1400,
            "source": "https://www.pixiv.net/artworks/321",
        }
    ]

    def test_match_and_registration(self):
        assert MoebooruResolver().match(self.URL)
        assert not MoebooruResolver().match("https://yande.re/post?tags=x")
        assert isinstance(get_resolver(self.URL), MoebooruResolver)

    async def test_resolve(self):
        async with _client(self.BODY) as client:
            post = await MoebooruResolver().resolve(self.URL, client)
        assert post.canonical_url == "https://www.pixiv.net/artworks/321"
        assert post.images[0].width == 2000

    async def test_empty_list_not_found(self):
        async with _client([]) as client:
            with pytest.raises(PostNotFoundError):
                await MoebooruResolver().resolve(self.URL, client)

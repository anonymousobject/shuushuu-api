"""Tests for the danbooru / gelbooru / yande.re resolvers."""

import httpx
import pytest

from app.config import settings
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

    async def test_resolve_appends_credentials_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "DANBOORU_LOGIN", "fakeuser")
        monkeypatch.setattr(settings, "DANBOORU_API_KEY", "fakekey123")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json=self.POST)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await DanbooruResolver().resolve(self.URL, client)
        assert "login=fakeuser" in seen["url"]
        assert "api_key=fakekey123" in seen["url"]

    async def test_resolve_omits_credentials_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(settings, "DANBOORU_LOGIN", "")
        monkeypatch.setattr(settings, "DANBOORU_API_KEY", "")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json=self.POST)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await DanbooruResolver().resolve(self.URL, client)
        assert "login" not in seen["url"]
        assert "api_key" not in seen["url"]


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

    def test_registration_follows_credential_config(self, monkeypatch):
        import importlib

        from app.config import settings
        from app.services.url_import import registry as registry_module

        url = "https://gelbooru.com/index.php?page=post&s=view&id=1"
        try:
            monkeypatch.setattr(settings, "GELBOORU_API_KEY", "")
            monkeypatch.setattr(settings, "GELBOORU_USER_ID", "")
            importlib.reload(registry_module)
            assert registry_module.get_resolver(url) is None
            monkeypatch.setattr(settings, "GELBOORU_API_KEY", "k")
            monkeypatch.setattr(settings, "GELBOORU_USER_ID", "1")
            importlib.reload(registry_module)
            assert registry_module.get_resolver(url) is not None
        finally:
            monkeypatch.undo()
            importlib.reload(registry_module)

    async def test_resolve(self):
        async with _client(self.BODY) as client:
            post = await GelbooruResolver().resolve(self.URL, client)
        assert post.canonical_url == self.URL
        assert post.images[0].full_url == self.BODY["post"][0]["file_url"]
        assert post.images[0].headers == {"Referer": "https://gelbooru.com/"}

    async def test_empty_post_list_not_found(self):
        async with _client({"post": []}) as client:
            with pytest.raises(PostNotFoundError):
                await GelbooruResolver().resolve(self.URL, client)

    async def test_resolve_appends_credentials_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "GELBOORU_API_KEY", "abc123")
        monkeypatch.setattr(settings, "GELBOORU_USER_ID", "42")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json=self.BODY)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await GelbooruResolver().resolve(self.URL, client)
        assert "api_key=abc123" in seen["url"]
        assert "user_id=42" in seen["url"]

    async def test_resolve_omits_credentials_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(settings, "GELBOORU_API_KEY", "")
        monkeypatch.setattr(settings, "GELBOORU_USER_ID", "")
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json=self.BODY)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await GelbooruResolver().resolve(self.URL, client)
        assert "api_key" not in seen["url"]
        assert "user_id" not in seen["url"]


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

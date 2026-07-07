"""Tests for the pixiv resolver (fixtures mirror the live ajax API, spike-verified 2026-07-06)."""

import httpx
import pytest

from app.services.url_import.base import (
    PostNotFoundError,
    RestrictedContentError,
    UpstreamError,
)
from app.services.url_import.pixiv import PixivResolver
from app.services.url_import.registry import get_resolver


def _illust_body(**overrides):
    body = {
        "title": "ミヨちゃん",
        "userName": "horagen",
        "userId": "80088843",
        "pageCount": 1,
        "xRestrict": 0,
        "illustType": 0,
        "width": 1488,
        "height": 2105,
        "urls": {
            "small": "https://i.pximg.net/c/540x540_70/img-master/img/x/138823691_p0_master1200.jpg",
            "original": "https://i.pximg.net/img-original/img/x/138823691_p0.png",
        },
    }
    body.update(overrides)
    return body


def _client(illust_body, pages_body=None):
    def handler(request):
        path = request.url.path
        if path == "/ajax/illust/138823691":
            return httpx.Response(200, json={"error": False, "body": illust_body})
        if path == "/ajax/illust/138823691/pages":
            return httpx.Response(200, json={"error": False, "body": pages_body or []})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestMatch:
    def test_matches_plain_and_language_urls(self):
        resolver = PixivResolver()
        assert resolver.match("https://www.pixiv.net/artworks/138823691")
        assert resolver.match("https://www.pixiv.net/en/artworks/138823691")
        assert resolver.match("https://pixiv.net/artworks/138823691")

    def test_rejects_other_pixiv_urls(self):
        resolver = PixivResolver()
        assert not resolver.match("https://www.pixiv.net/users/80088843")
        assert not resolver.match("https://example.com/artworks/1")

    def test_registered(self):
        assert isinstance(get_resolver("https://www.pixiv.net/en/artworks/138823691"), PixivResolver)


class TestResolve:
    async def test_single_page(self):
        seen_referers = []

        def handler(request):
            if request.url.path == "/ajax/illust/138823691":
                seen_referers.append(request.headers.get("referer"))
                return httpx.Response(200, json={"error": False, "body": _illust_body()})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            post = await PixivResolver().resolve(
                "https://www.pixiv.net/en/artworks/138823691", client
            )
        assert post.site == "pixiv"
        assert post.canonical_url == "https://www.pixiv.net/artworks/138823691"
        assert post.title == "ミヨちゃん"
        assert post.artist_name == "horagen"
        assert post.artist_id == "80088843"
        assert len(post.images) == 1
        assert post.images[0].full_url.endswith("_p0.png")
        assert post.images[0].headers == {"Referer": "https://www.pixiv.net/"}
        assert seen_referers == ["https://www.pixiv.net/"]

    async def test_multi_page_uses_pages_endpoint(self):
        pages = [
            {"urls": {"original": f"https://i.pximg.net/img-original/img/x/138823691_p{i}.png",
                      "small": f"https://i.pximg.net/c/540x540_70/img-master/img/x/138823691_p{i}_master1200.jpg"},
             "width": 100, "height": 200}
            for i in range(3)
        ]
        async with _client(_illust_body(pageCount=3), pages) as client:
            post = await PixivResolver().resolve(
                "https://www.pixiv.net/artworks/138823691", client
            )
        assert [img.full_url for img in post.images] == [p["urls"]["original"] for p in pages]
        assert post.images[1].width == 100

    async def test_r18_rejected(self):
        async with _client(_illust_body(xRestrict=1)) as client:
            with pytest.raises(RestrictedContentError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_ugoira_rejected(self):
        async with _client(_illust_body(illustType=2)) as client:
            with pytest.raises(RestrictedContentError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_error_payload_raises_not_found(self):
        def handler(request):
            return httpx.Response(200, json={"error": True, "message": "該当作品は削除されたか、存在しない作品IDです。", "body": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PostNotFoundError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_pages_error_payload_raises_not_found(self):
        def handler(request):
            path = request.url.path
            if path == "/ajax/illust/138823691":
                return httpx.Response(200, json={"error": False, "body": _illust_body(pageCount=3)})
            if path == "/ajax/illust/138823691/pages":
                return httpx.Response(
                    200, json={"error": True, "message": "該当作品は削除されたか、存在しない作品IDです。", "body": []}
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PostNotFoundError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_illust_body_missing_urls_raises_upstream_error(self):
        body = _illust_body()
        del body["urls"]
        async with _client(body) as client:
            with pytest.raises(UpstreamError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_off_host_original_url_rejects_whole_post(self):
        body = _illust_body(urls={
            "small": "https://i.pximg.net/c/540x540_70/img-master/img/x/138823691_p0_master1200.jpg",
            "original": "https://evil.example/a.png",
        })
        async with _client(body) as client:
            with pytest.raises(UpstreamError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_off_host_thumb_url_rejects_whole_post(self):
        body = _illust_body(urls={
            "small": "https://evil.example/thumb.jpg",
            "original": "https://i.pximg.net/img-original/img/x/138823691_p0.png",
        })
        async with _client(body) as client:
            with pytest.raises(UpstreamError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

    async def test_off_host_page_url_rejects_whole_post(self):
        pages = [
            {"urls": {"original": "https://i.pximg.net/img-original/img/x/138823691_p0.png",
                      "small": "https://i.pximg.net/c/540x540_70/img-master/img/x/138823691_p0_master1200.jpg"},
             "width": 100, "height": 200},
            {"urls": {"original": "https://evil.example/p1.png",
                      "small": "https://i.pximg.net/c/540x540_70/img-master/img/x/138823691_p1_master1200.jpg"},
             "width": 100, "height": 200},
        ]
        async with _client(_illust_body(pageCount=2), pages) as client:
            with pytest.raises(UpstreamError):
                await PixivResolver().resolve("https://www.pixiv.net/artworks/138823691", client)

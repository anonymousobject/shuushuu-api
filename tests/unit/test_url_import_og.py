"""Tests for OG-tag extraction and the zerochan / ko-fi resolvers."""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, UpstreamError
from app.services.url_import.kofi import KofiResolver
from app.services.url_import.og import extract_og_tags, fetch_og_page
from app.services.url_import.registry import get_resolver
from app.services.url_import.zerochan import ZerochanResolver


def _page(og_image, og_title="A Title"):
    return f"""<html><head>
    <meta property="og:title" content="{og_title}">
    <meta property="og:image" content="{og_image}">
    </head><body></body></html>"""


class TestExtractOgTags:
    def test_extracts_property_content_pairs(self):
        tags = extract_og_tags(_page("https://static.zerochan.net/full/1.jpg"))
        assert tags["image"] == "https://static.zerochan.net/full/1.jpg"
        assert tags["title"] == "A Title"

    def test_content_before_property_order(self):
        html = '<meta content="https://x.test/a.png" property="og:image">'
        assert extract_og_tags(html)["image"] == "https://x.test/a.png"

    def test_unescapes_entities(self):
        html = '<meta property="og:image" content="https://x.test/a.png?a=1&amp;b=2">'
        assert extract_og_tags(html)["image"] == "https://x.test/a.png?a=1&b=2"

    def test_double_quoted_content_with_apostrophe_extracted_fully(self):
        html = '<meta property="og:title" content="Fan\'s OC">'
        assert extract_og_tags(html)["title"] == "Fan's OC"


class TestFetchOgPage:
    async def test_off_host_redirect_refused(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://evil.example/x"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UpstreamError):
                await fetch_og_page(
                    client, "https://www.zerochan.net/123", site="zerochan",
                    allowed_hosts={"www.zerochan.net", "zerochan.net"},
                )

    async def test_same_host_redirect_followed(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(301, headers={"location": "https://www.zerochan.net/123?v=2"})
            return httpx.Response(200, text=_page("https://static.zerochan.net/full/1.jpg"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tags = await fetch_og_page(
                client, "https://www.zerochan.net/123", site="zerochan",
                allowed_hosts={"www.zerochan.net", "zerochan.net"},
            )
        assert tags["image"].endswith("/full/1.jpg")
        assert len(calls) == 2


class TestZerochan:
    URL = "https://www.zerochan.net/4321"

    def test_match(self):
        assert ZerochanResolver().match(self.URL)
        assert ZerochanResolver().match("https://zerochan.net/4321")
        assert not ZerochanResolver().match("https://www.zerochan.net/Original")
        assert isinstance(get_resolver(self.URL), ZerochanResolver)

    async def test_resolve(self):
        def handler(request):
            return httpx.Response(200, text=_page("https://static.zerochan.net/Full.4321.jpg", "Miyo (Artist X)"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            post = await ZerochanResolver().resolve(self.URL, client)
        assert post.images[0].full_url == "https://static.zerochan.net/Full.4321.jpg"
        assert post.title == "Miyo (Artist X)"
        assert post.canonical_url == "https://www.zerochan.net/4321"

    async def test_off_site_og_image_refused(self):
        def handler(request):
            return httpx.Response(200, text=_page("https://evil.example/a.jpg"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UpstreamError):
                await ZerochanResolver().resolve(self.URL, client)

    async def test_missing_og_image_not_found(self):
        def handler(request):
            return httpx.Response(200, text="<html><head></head></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PostNotFoundError):
                await ZerochanResolver().resolve(self.URL, client)


class TestKofi:
    URL = "https://ko-fi.com/i/IX8X0ABC12"

    def test_match(self):
        assert KofiResolver().match(self.URL)
        assert KofiResolver().match("https://ko-fi.com/post/Some-Post-A0A0ABC")
        assert not KofiResolver().match("https://ko-fi.com/someartist")
        assert isinstance(get_resolver(self.URL), KofiResolver)

    async def test_resolve(self):
        def handler(request):
            return httpx.Response(200, text=_page("https://storage.ko-fi.com/cdn/useruploads/post/x.png", "Fanart!"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            post = await KofiResolver().resolve(self.URL, client)
        assert post.images[0].full_url == "https://storage.ko-fi.com/cdn/useruploads/post/x.png"
        assert post.title == "Fanart!"
        assert post.canonical_url == "https://ko-fi.com/i/IX8X0ABC12"

    async def test_www_and_bare_forms_resolve_to_same_canonical_url(self):
        fetched_urls = []

        def handler(request):
            fetched_urls.append(str(request.url))
            return httpx.Response(200, text=_page("https://storage.ko-fi.com/cdn/useruploads/post/x.png"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bare_post = await KofiResolver().resolve("https://ko-fi.com/i/IX8X0ABC12", client)
            www_post = await KofiResolver().resolve("https://www.ko-fi.com/i/IX8X0ABC12", client)
        assert bare_post.canonical_url == www_post.canonical_url == "https://ko-fi.com/i/IX8X0ABC12"
        assert fetched_urls == [
            "https://ko-fi.com/i/IX8X0ABC12",
            "https://ko-fi.com/i/IX8X0ABC12",
        ]

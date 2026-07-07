"""Tests for OG-tag extraction and the zerochan / ko-fi resolvers."""

import httpx
import pytest

from app.config import settings
from app.services.url_import.base import (
    BROWSER_USER_AGENT,
    TOOL_USER_AGENT,
    UpstreamError,
)
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

    def test_name_attribute_form_extracted(self):
        # zerochan uses <meta name="og:..."> rather than property=
        tags = extract_og_tags(
            '<meta name="og:title" content="Lilith #4706056">\n'
            '<meta name="og:image" content="https://static.zerochan.net/Lilith.full.4706056.jpg">'
        )
        assert tags["image"] == "https://static.zerochan.net/Lilith.full.4706056.jpg"
        assert tags["title"] == "Lilith #4706056"

    def test_name_attribute_form_content_before_name_order(self):
        html = '<meta content="https://x.test/a.png" name="og:image">'
        assert extract_og_tags(html)["image"] == "https://x.test/a.png"


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

    async def test_default_user_agent_is_browser_ua(self):
        seen = {}

        def handler(request):
            seen["user_agent"] = request.headers.get("user-agent")
            return httpx.Response(200, text=_page("https://static.zerochan.net/full/1.jpg"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_og_page(
                client, "https://www.zerochan.net/123", site="zerochan",
                allowed_hosts={"www.zerochan.net", "zerochan.net"},
            )
        assert seen["user_agent"] == BROWSER_USER_AGENT

    async def test_custom_user_agent_is_used_when_provided(self):
        seen = {}

        def handler(request):
            seen["user_agent"] = request.headers.get("user-agent")
            return httpx.Response(200, text=_page("https://static.zerochan.net/full/1.jpg"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_og_page(
                client, "https://www.zerochan.net/123", site="zerochan",
                allowed_hosts={"www.zerochan.net", "zerochan.net"},
                user_agent=TOOL_USER_AGENT,
            )
        assert seen["user_agent"] == TOOL_USER_AGENT


class TestZerochan:
    URL = "https://www.zerochan.net/4321"
    ENTRY = {
        "id": 4321,
        "small": "https://s1.zerochan.net/75/06/21/4321.jpg",
        "large": "https://s1.zerochan.net/Miyo.600.4321.jpg",
        "full": "https://static.zerochan.net/Miyo.full.4321.png",
        "width": 2382,
        "height": 5033,
        "source": "https://www.pixiv.net/en/artworks/999",
        "primary": "Miyo (Artist X)",
    }

    def _client(self, entry, seen=None):
        def handler(request):
            if seen is not None:
                seen["url"] = str(request.url)
                seen["user_agent"] = request.headers.get("user-agent")
            return httpx.Response(200, json=entry)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_match(self):
        assert ZerochanResolver().match(self.URL)
        assert ZerochanResolver().match("https://zerochan.net/4321")
        assert not ZerochanResolver().match("https://www.zerochan.net/Original")
        assert isinstance(get_resolver(self.URL), ZerochanResolver)

    async def test_resolve(self, monkeypatch):
        monkeypatch.setattr(settings, "ZEROCHAN_USERNAME", "")
        seen = {}
        async with self._client(self.ENTRY, seen) as client:
            post = await ZerochanResolver().resolve(self.URL, client)
        assert seen["url"] == "https://www.zerochan.net/4321?json"
        assert seen["user_agent"] == TOOL_USER_AGENT
        image = post.images[0]
        assert image.full_url == "https://static.zerochan.net/Miyo.full.4321.png"
        assert image.thumb_url == "https://s1.zerochan.net/Miyo.600.4321.jpg"
        assert image.width == 2382
        assert image.height == 5033
        assert image.headers == {"User-Agent": TOOL_USER_AGENT}
        assert post.title == "Miyo (Artist X)"

    async def test_resolve_prefers_upstream_source_as_canonical(self):
        async with self._client(self.ENTRY) as client:
            post = await ZerochanResolver().resolve(self.URL, client)
        assert post.canonical_url == "https://www.pixiv.net/en/artworks/999"

    async def test_missing_source_falls_back_to_entry_page(self):
        entry = {k: v for k, v in self.ENTRY.items() if k != "source"}
        async with self._client(entry) as client:
            post = await ZerochanResolver().resolve(self.URL, client)
        assert post.canonical_url == "https://www.zerochan.net/4321"

    async def test_off_site_full_image_refused(self):
        async with self._client({**self.ENTRY, "full": "https://evil.example/a.png"}) as client:
            with pytest.raises(UpstreamError):
                await ZerochanResolver().resolve(self.URL, client)

    async def test_off_site_thumb_image_refused(self):
        async with self._client({**self.ENTRY, "large": "https://evil.example/t.jpg"}) as client:
            with pytest.raises(UpstreamError):
                await ZerochanResolver().resolve(self.URL, client)

    async def test_missing_full_is_upstream_error(self):
        entry = {k: v for k, v in self.ENTRY.items() if k != "full"}
        async with self._client(entry) as client:
            with pytest.raises(UpstreamError):
                await ZerochanResolver().resolve(self.URL, client)

    async def test_configured_username_builds_documented_ua(self, monkeypatch):
        monkeypatch.setattr(settings, "ZEROCHAN_USERNAME", "shuu_admin")
        seen = {}
        async with self._client(self.ENTRY, seen) as client:
            post = await ZerochanResolver().resolve(self.URL, client)
        assert seen["user_agent"] == "shuushuu-url-import/1.0 - shuu_admin"
        assert post.images[0].headers == {"User-Agent": "shuushuu-url-import/1.0 - shuu_admin"}


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

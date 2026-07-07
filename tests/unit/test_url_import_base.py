"""Tests for the url_import base types, fetch_json helper, and registry."""

import httpx
import pytest

from app.services.url_import.base import (
    BROWSER_USER_AGENT,
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
    fetch_json,
    host_allowed,
)
from app.services.url_import.registry import get_resolver, supported_sites


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestFetchJson:
    async def test_returns_parsed_json_and_sends_browser_ua(self):
        seen = {}

        def handler(request):
            seen["ua"] = request.headers["user-agent"]
            return httpx.Response(200, json={"ok": True})

        async with _client(handler) as client:
            data = await fetch_json(client, "https://example.test/api", site="example")
        assert data == {"ok": True}
        assert seen["ua"] == BROWSER_USER_AGENT

    async def test_extra_headers_are_sent(self):
        seen = {}

        def handler(request):
            seen["referer"] = request.headers.get("referer")
            return httpx.Response(200, json={})

        async with _client(handler) as client:
            await fetch_json(
                client, "https://example.test/api", site="example",
                headers={"Referer": "https://example.test/"},
            )
        assert seen["referer"] == "https://example.test/"

    async def test_404_raises_post_not_found(self):
        async with _client(lambda r: httpx.Response(404)) as client:
            with pytest.raises(PostNotFoundError):
                await fetch_json(client, "https://example.test/api", site="example")

    async def test_500_raises_upstream_error(self):
        async with _client(lambda r: httpx.Response(500)) as client:
            with pytest.raises(UpstreamError):
                await fetch_json(client, "https://example.test/api", site="example")

    async def test_network_error_raises_upstream_error(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        async with _client(handler) as client:
            with pytest.raises(UpstreamError):
                await fetch_json(client, "https://example.test/api", site="example")

    async def test_invalid_json_raises_upstream_error(self):
        async with _client(lambda r: httpx.Response(200, text="<html>")) as client:
            with pytest.raises(UpstreamError):
                await fetch_json(client, "https://example.test/api", site="example")


class TestHostAllowed:
    def test_exact_match(self):
        assert host_allowed("https://gelbooru.com/index.php", "gelbooru.com")

    def test_subdomain_match(self):
        assert host_allowed("https://cdn.donmai.us/original/x.jpg", "donmai.us")

    def test_leading_dot_rejects_lookalike_domain(self):
        # "evilgelbooru.com" ends with "gelbooru.com" as a raw string, but not
        # as a subdomain -- the check must require a "." boundary.
        assert not host_allowed("https://evilgelbooru.com/x.jpg", "gelbooru.com")

    def test_empty_host_rejected(self):
        assert not host_allowed("not-a-url", "gelbooru.com")

    def test_checks_multiple_allowed_hosts(self):
        assert host_allowed("https://i.pximg.net/x.png", "donmai.us", "pximg.net")
        assert not host_allowed("https://evil.example/x.png", "donmai.us", "pximg.net")


class TestRegistry:
    def test_unknown_url_returns_none(self):
        assert get_resolver("https://not-a-supported-site.example/post/1") is None

    def test_supported_sites_is_a_list(self):
        assert isinstance(supported_sites(), list)


def test_resolved_types_construct():
    image = ResolvedImage(full_url="https://example.test/a.png")
    post = ResolvedPost(site="example", canonical_url="https://example.test/1", images=[image])
    assert post.images[0].headers == {}
    assert post.title is None

"""Tests for the bluesky resolver (public AT Protocol API).

Fixtures mirror the live public.api.bsky.app `getPostThread` response shape,
spike-verified 2026-07-06 against a real post (see task-8-report.md for the
raw commands/output). One material finding from that spike: AT Protocol
reports "post not found" as an XRPC-level error -- HTTP 400 with a JSON body
of `{"error": "NotFound", ...}` -- rather than a plain HTTP 404. That doesn't
fit the shared `fetch_json` helper's 404-only "not found" mapping, so this
resolver does its own request/response handling instead of delegating to it
(the same pattern `og.py`'s `fetch_og_page` uses for its own reasons).
"""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, UpstreamError
from app.services.url_import.bluesky import BlueskyResolver, _with_jpeg_suffix
from app.services.url_import.registry import get_resolver

URL = "https://bsky.app/profile/artist.bsky.social/post/3kabc123xyz"


def _thread_body(images):
    return {
        "thread": {
            "$type": "app.bsky.feed.defs#threadViewPost",
            "post": {
                "uri": "at://did:plc:xyz/app.bsky.feed.post/3kabc123xyz",
                "author": {"handle": "artist.bsky.social", "displayName": "The Artist"},
                "embed": {
                    "$type": "app.bsky.embed.images#view",
                    "images": images,
                },
            },
        }
    }


def _client(json_body, status=200):
    def handler(request):
        assert request.url.host == "public.api.bsky.app"
        assert "app.bsky.feed.getPostThread" in request.url.path
        return httpx.Response(status, json=json_body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestMatch:
    def test_matches_post_urls(self):
        resolver = BlueskyResolver()
        assert resolver.match(URL)
        assert not resolver.match("https://bsky.app/profile/artist.bsky.social")
        assert isinstance(get_resolver(URL), BlueskyResolver)


class TestResolve:
    async def test_resolves_image_embeds(self):
        images = [
            {
                "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/abc@jpeg",
                "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc@jpeg",
                "aspectRatio": {"width": 1600, "height": 1200},
            }
        ]
        async with _client(_thread_body(images)) as client:
            post = await BlueskyResolver().resolve(URL, client)
        assert post.images[0].full_url.endswith("feed_fullsize/plain/did:plc:xyz/abc@jpeg")
        assert post.images[0].width == 1600
        assert post.images[0].height == 1200
        assert post.artist_name == "The Artist"
        assert post.artist_id == "artist.bsky.social"
        assert post.canonical_url == URL

    async def test_record_with_media_embed_fallback(self):
        # Quote-posts with attached media nest images under embed.media
        # (app.bsky.embed.recordWithMedia#view) rather than embed.images.
        images = [
            {
                "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/def@jpeg",
                "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/def@jpeg",
                "aspectRatio": {"width": 800, "height": 600},
            }
        ]
        body = _thread_body([])
        body["thread"]["post"]["embed"] = {
            "$type": "app.bsky.embed.recordWithMedia#view",
            "media": {"$type": "app.bsky.embed.images#view", "images": images},
            "record": {"record": {"$type": "app.bsky.embed.record#viewRecord"}},
        }
        async with _client(body) as client:
            post = await BlueskyResolver().resolve(URL, client)
        assert len(post.images) == 1
        assert post.images[0].full_url.endswith("feed_fullsize/plain/did:plc:xyz/def@jpeg")

    async def test_fullsize_without_suffix_gets_jpeg_appended(self):
        # Bluesky's plain fullsize is already a downscaled, lossy render (not
        # the original) -- so @jpeg (small, directly uploadable, no transcode
        # needed) beats @png (8x the bytes for pixel-perfection of a render
        # that was never the original anyway).
        images = [
            {
                "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/abc@jpeg",
                "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc",
                "aspectRatio": {"width": 1600, "height": 1200},
            }
        ]
        async with _client(_thread_body(images)) as client:
            post = await BlueskyResolver().resolve(URL, client)
        assert post.images[0].full_url == (
            "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc@jpeg"
        )
        # Thumbs are display-only; browsers render webp fine, so leave untouched.
        assert post.images[0].thumb_url == (
            "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/abc@jpeg"
        )

    async def test_fullsize_with_existing_suffix_left_unchanged(self):
        images = [
            {
                "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/abc@jpeg",
                "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc@jpeg",
            }
        ]
        async with _client(_thread_body(images)) as client:
            post = await BlueskyResolver().resolve(URL, client)
        assert post.images[0].full_url == (
            "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc@jpeg"
        )

    def test_with_jpeg_suffix_trailing_slash_left_unchanged(self):
        url = "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/"
        assert _with_jpeg_suffix(url) == url

    async def test_artist_name_falls_back_to_handle(self):
        images = [
            {
                "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:xyz/abc@jpeg",
                "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:xyz/abc@jpeg",
            }
        ]
        body = _thread_body(images)
        body["thread"]["post"]["author"] = {"handle": "artist.bsky.social"}
        async with _client(body) as client:
            post = await BlueskyResolver().resolve(URL, client)
        assert post.artist_name == "artist.bsky.social"

    async def test_no_images_raises_not_found(self):
        body = _thread_body([])
        body["thread"]["post"]["embed"] = None
        async with _client(body) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(URL, client)

    async def test_not_found_thread(self):
        # Lexicon-documented union member for a post referenced within a
        # thread that no longer exists. Not reproducible live for a root
        # lookup (see test_not_found_via_xrpc_error below for what the real
        # API actually returns in that case) but kept as defensive coverage
        # since it's a valid `thread` shape per the app.bsky.feed.defs lexicon.
        body = {"thread": {"$type": "app.bsky.feed.defs#notFoundPost", "notFound": True}}
        async with _client(body) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(URL, client)

    async def test_not_found_via_xrpc_error(self):
        # Real, spike-verified behavior: a genuinely nonexistent/deleted post
        # returns HTTP 400 with {"error": "NotFound", ...}, not a plain 404.
        body = {
            "error": "NotFound",
            "message": "Post not found: at://did:plc:xyz/app.bsky.feed.post/3kabc123xyz",
        }
        async with _client(body, status=400) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(URL, client)

    async def test_other_xrpc_errors_raise_upstream_error(self):
        # A different XRPC error name (e.g. malformed at-uri) must not be
        # mistaken for "not found".
        body = {"error": "InvalidRequest", "message": "Invalid at-uri"}
        async with _client(body, status=400) as client:
            with pytest.raises(UpstreamError):
                await BlueskyResolver().resolve(URL, client)

    async def test_server_error_raises_upstream_error(self):
        async with _client({}, status=500) as client:
            with pytest.raises(UpstreamError):
                await BlueskyResolver().resolve(URL, client)

    async def test_network_error_raises_upstream_error(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UpstreamError):
                await BlueskyResolver().resolve(URL, client)

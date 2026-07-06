"""Tests for the Bluesky (AT Protocol) resolver.

Fixtures mirror the live Bluesky API (xrpc endpoints) spike-verified 2026-07-06.
The resolver fetches from public.api.bsky.app without authentication.
"""

import httpx
import pytest

from app.services.url_import.base import PostNotFoundError, UpstreamError
from app.services.url_import.bluesky import BlueskyResolver
from app.services.url_import.registry import get_resolver

PROFILE_URL = "https://bsky.app/profile/artist.bsky.social/post/3kwjf2rlbih23"


def _post_record(handle="artist.bsky.social", did="did:plc:example123", image_count=1, **overrides):
    """Fixture for a minimal valid post record from app.bsky.feed.post."""
    images = [
        {
            "image": {
                "mime": "image/png",
                "size": 12345,
                "cid": f"bafyreif{i:06d}",  # Synthetic CID for testing
            },
            "alt": f"Image description {i}",
        }
        for i in range(image_count)
    ]
    post = {
        "uri": f"at://{did}/app.bsky.feed.post/3kwjf2rlbih23",
        "cid": "bafyreiafakepost",
        "author": {
            "did": did,
            "handle": handle,
            "displayName": "Example Artist",
            "avatar": "https://cdn.bsky.app/img/avatar/plain/abcd/efgh.jpeg",
        },
        "record": {
            "text": "Check out my new artwork!",
            "createdAt": "2024-07-06T12:34:56.000Z",
            "embed": {
                "type": "app.bsky.embed.images",
                "images": images,
            },
        },
        "indexedAt": "2024-07-06T12:35:00.000Z",
    }
    post.update(overrides)
    return post


def _getPostThread_response(post=None, **overrides):
    """Fixture for a full getPostThread response with thread structure."""
    if post is None:
        post = _post_record()
    response = {
        "thread": {
            "uri": post["uri"],
            "cid": post["cid"],
            "author": post["author"],
            "record": post["record"],
            "indexedAt": post["indexedAt"],
            "viewer": {"muted": False, "blocked": False},
        }
    }
    response.update(overrides)
    return response


def _client(thread_response=None, status=200, not_found=False):
    """Create a mock httpx.AsyncClient with Bluesky API responses.

    Handles both resolveHandle and getPostThread endpoints.
    """

    def handler(request):
        # All Bluesky XRPC calls go through public.api.bsky.app
        assert request.url.host == "public.api.bsky.app"

        if not_found:
            return httpx.Response(404)

        if status != 200:
            return httpx.Response(status, json={"error": "some_error"})

        # Route to appropriate endpoint
        path = request.url.path
        if "resolveHandle" in str(request.url):
            # Return the DID for the handle
            return httpx.Response(200, json={"did": "did:plc:example123"})

        if "getPostThread" in str(request.url):
            # Return the post thread
            return httpx.Response(200, json=thread_response or _getPostThread_response())

        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestMatch:
    def test_matches_bsky_app_profile_post_urls(self):
        resolver = BlueskyResolver()
        assert resolver.match("https://bsky.app/profile/artist.bsky.social/post/3kwjf2rlbih23")
        assert resolver.match("http://bsky.app/profile/someone/post/abc123xyz")
        assert resolver.match("https://bsky.app/profile/handle.bsky.social/post/3kwjf2rlbih23")

    def test_rejects_other_bsky_urls(self):
        resolver = BlueskyResolver()
        assert not resolver.match("https://bsky.app/profile/artist.bsky.social")
        assert not resolver.match("https://bsky.app/feed/timeline")
        assert not resolver.match("https://example.com/profile/someone/post/abc123")

    def test_registered(self):
        assert isinstance(get_resolver(PROFILE_URL), BlueskyResolver)


class TestResolve:
    async def test_single_image_extraction(self):
        post = _post_record(image_count=1)
        async with _client(_getPostThread_response(post=post)) as client:
            result = await BlueskyResolver().resolve(PROFILE_URL, client)

        assert result.site == "bluesky"
        assert result.canonical_url == PROFILE_URL
        assert result.artist_name == "Example Artist"
        assert result.artist_id == "artist.bsky.social"
        assert len(result.images) == 1
        assert "cdn.bsky.app" in result.images[0].full_url
        assert "feed_fullsize" in result.images[0].full_url
        assert "feed_thumbnail" in result.images[0].thumb_url

    async def test_multi_image_extraction(self):
        post = _post_record(image_count=3)
        async with _client(_getPostThread_response(post=post)) as client:
            result = await BlueskyResolver().resolve(PROFILE_URL, client)

        assert len(result.images) == 3
        assert all("cdn.bsky.app" in img.full_url for img in result.images)
        # Verify all images have proper CDN structure
        for img in result.images:
            assert "feed_fullsize" in img.full_url
            assert "feed_thumbnail" in img.thumb_url

    async def test_post_not_found(self):
        async with _client(not_found=True) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(PROFILE_URL, client)

    async def test_upstream_error_on_http_error(self):
        async with _client(status=500) as client:
            with pytest.raises(UpstreamError):
                await BlueskyResolver().resolve(PROFILE_URL, client)

    async def test_no_images_raises_not_found(self):
        # Post with no embed or empty images array
        post = _post_record(image_count=0)
        post["record"]["embed"] = {"type": "app.bsky.embed.images", "images": []}
        async with _client(_getPostThread_response(post=post)) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(PROFILE_URL, client)

    async def test_post_without_images_embed_raises_not_found(self):
        # Post with text-only embed (no images)
        post = _post_record()
        post["record"]["embed"] = {"type": "app.bsky.embed.text"}  # No images
        async with _client(_getPostThread_response(post=post)) as client:
            with pytest.raises(PostNotFoundError):
                await BlueskyResolver().resolve(PROFILE_URL, client)

    async def test_missing_author_fields(self):
        # Handle graceful degradation when author fields are missing
        post = _post_record()
        post["author"]["displayName"] = None  # Missing artist_name
        async with _client(_getPostThread_response(post=post)) as client:
            result = await BlueskyResolver().resolve(PROFILE_URL, client)

        assert result.artist_id == "artist.bsky.social"
        assert result.artist_name is None  # Gracefully None
        assert len(result.images) == 1

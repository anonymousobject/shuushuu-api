"""TTL-cached serialized responses for the logged-out image feed.

For an anonymous request, ``list_images`` has no viewer-specific state (favorites,
reports, moderation, display preferences all hang off the logged-in user), so the
query string fully determines the response. Bots and scrapers hammer the same feed
pages, so we cache the serialized JSON body keyed on the query string. Logged-in
requests never touch this cache. The cached page can lag an upload, edit, status
change, or comment by up to the TTL, which is acceptable for a public feed and not
worth the invalidation coupling.
"""

import hashlib
from urllib.parse import urlencode

import redis.asyncio as redis
from fastapi import Request

# TTL-only — no per-mutation invalidation. A page can lag a create/edit/status
# change by at most FEED_RESPONSE_TTL seconds.
FEED_RESPONSE_TTL = 30

_KEY_PREFIX = "feed:response:"


def feed_response_key(request: Request) -> str:
    """Cache key for a request: hash of its query params, insensitive to their order."""
    material = urlencode(sorted(request.query_params.multi_items()))
    return _KEY_PREFIX + hashlib.sha256(material.encode()).hexdigest()


async def get_cached_feed_response(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    key: str,
) -> str | None:
    """Return the stored JSON body for ``key``, or None on a miss."""
    cached: str | None = await redis_client.get(key)
    return cached


async def cache_feed_response(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    key: str,
    body: str,
) -> None:
    """Store the serialized JSON body for ``key`` for FEED_RESPONSE_TTL seconds."""
    await redis_client.setex(key, FEED_RESPONSE_TTL, body)

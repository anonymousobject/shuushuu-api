"""Bluesky (AT Protocol) resolver: uses the public Bluesky API without authentication.

The Bluesky post URL pattern is:
  https://bsky.app/profile/{handle}/post/{rkey}

To resolve:
1. Extract handle and rkey from the URL
2. Call getProfile to resolve handle -> DID
3. Call getPostThread with the full AT URI (at://did/app.bsky.feed.post/{rkey})
4. Extract images from the embed.images array in the post record

Bluesky images are CID-based blobs served from cdn.bsky.app with URLs like:
  https://cdn.bsky.app/img/feed_fullsize/{did}/{cid}
  https://cdn.bsky.app/img/feed_thumbnail/{did}/{cid}
"""

import re
from typing import Any

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    fetch_json,
)

# Match: bsky.app/profile/{handle}/post/{rkey}
_URL_RE = re.compile(r"^https?://bsky\.app/profile/([^/]+)/post/([^/?]+)")
_XRPC_BASE = "https://public.api.bsky.app/xrpc"


class BlueskyResolver:
    site = "bluesky"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None  # caller guarantees match()
        handle, rkey = match.group(1), match.group(2)

        # Step 1: Resolve handle to DID
        profile_data = await fetch_json(
            client,
            f"{_XRPC_BASE}/com.atproto.identity.resolveHandle?handle={handle}",
            site=self.site,
        )
        did = profile_data.get("did")
        if not did:
            raise PostNotFoundError(f"Could not resolve Bluesky handle: {handle}")

        # Step 2: Fetch the post thread
        at_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        thread_data = await fetch_json(
            client,
            f"{_XRPC_BASE}/app.bsky.feed.getPostThread?uri={at_uri}&depth=0",
            site=self.site,
        )

        # Step 3: Extract post and author info
        thread = thread_data.get("thread", {})
        author = thread.get("author", {})
        record = thread.get("record", {})
        embed = record.get("embed", {})

        # Step 4: Extract images
        images = self._extract_images(embed, author.get("did"))
        if not images:
            raise PostNotFoundError("Bluesky post has no images")

        return ResolvedPost(
            site=self.site,
            canonical_url=url,
            images=images,
            title=record.get("text"),
            artist_name=author.get("displayName"),
            artist_id=author.get("handle"),
        )

    def _extract_images(self, embed: dict[str, Any], author_did: str | None) -> list[ResolvedImage]:
        """Extract ResolvedImage objects from an AT Protocol embed.images structure."""
        if embed.get("type") != "app.bsky.embed.images":
            return []

        images_data = embed.get("images", [])
        if not images_data:
            return []

        resolved = []
        for img_data in images_data:
            # The image blob is in img_data["image"]
            image_blob = img_data.get("image", {})
            cid = image_blob.get("cid")
            if not cid or not author_did:
                continue

            # Bluesky CDN URLs for images
            full_url = f"https://cdn.bsky.app/img/feed_fullsize/{author_did}/{cid}"
            thumb_url = f"https://cdn.bsky.app/img/feed_thumbnail/{author_did}/{cid}"

            resolved.append(
                ResolvedImage(
                    full_url=full_url,
                    thumb_url=thumb_url,
                    headers={},  # Bluesky CDN doesn't require special headers
                )
            )

        return resolved

"""Bluesky resolver via the public AT Protocol XRPC API (no auth required).

The Bluesky post URL pattern is:
  https://bsky.app/profile/{handle}/post/{rkey}

getPostThread accepts a handle directly in the at-uri (no separate
resolveHandle round-trip needed) and returns a hydrated view where the
author and embed live under thread.post, not thread itself -- the raw
record (thread.post.record) still carries unresolved blob refs, so images
must be read off thread.post.embed's hydrated fullsize/thumb/aspectRatio
fields, not fabricated from a cid.

AT Protocol represents a "post not found" outcome as an XRPC-level error --
HTTP 400 with a JSON body of {"error": "NotFound", ...} -- rather than a
plain HTTP 404, so this resolver makes its own request instead of routing
through the shared fetch_json helper (whose 404-only mapping doesn't fit
this API); spike-verified 2026-07-06 against public.api.bsky.app.
"""

import re
from urllib.parse import quote

import httpx

from app.services.url_import.base import (
    BROWSER_USER_AGENT,
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
)

_URL_RE = re.compile(r"^https?://bsky\.app/profile/([^/]+)/post/([A-Za-z0-9]+)")


def _with_png_suffix(fullsize_url: str) -> str:
    """Request the CDN's PNG sibling encode for suffix-less fullsize URLs.

    Bluesky's CDN serves webp for a fullsize URL whose final path segment
    carries no explicit format suffix -- but uploads here only accept
    jpg/png/gif. Appending "@png" asks the CDN for a sibling encode from
    the same master (live-verified: bare URL -> image/webp 19KB; @png ->
    image/png 265KB), which is not a transcode and loses no fidelity. If a
    suffix is already present (e.g. "@jpeg"), leave it alone. A URL ending
    in "/" (empty last segment) is left unchanged too -- defensive only,
    since real bluesky fullsize URLs are always CID-terminated.
    """
    last_segment = fullsize_url.rsplit("/", 1)[-1]
    if not last_segment or "@" in last_segment:
        return fullsize_url
    return f"{fullsize_url}@png"


class BlueskyResolver:
    site = "bluesky"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None  # caller guarantees match()
        handle, rkey = match.group(1), match.group(2)
        at_uri = f"at://{handle}/app.bsky.feed.post/{rkey}"
        api_url = (
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
            f"?uri={quote(at_uri, safe='')}&depth=0"
        )
        try:
            response = await client.get(api_url, headers={"User-Agent": BROWSER_USER_AGENT})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.site} request failed") from exc
        if response.status_code != 200:
            try:
                error_name = response.json().get("error")
            except ValueError:
                error_name = None
            if response.status_code == 404 or error_name == "NotFound":
                raise PostNotFoundError("bluesky post not found")
            raise UpstreamError(f"{self.site} returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamError(f"{self.site} returned invalid JSON") from exc

        thread = data.get("thread") or {}
        post = thread.get("post")
        if not post:
            raise PostNotFoundError("bluesky post not found")

        embed = post.get("embed") or {}
        image_views = embed.get("images") or []
        if not image_views and isinstance(embed.get("media"), dict):
            # record-with-media embeds (quote posts with attached photos)
            # nest the images one level down.
            image_views = embed["media"].get("images") or []
        if not image_views:
            raise PostNotFoundError("bluesky post has no images")

        images = [
            ResolvedImage(
                full_url=_with_png_suffix(view["fullsize"]),
                thumb_url=view.get("thumb"),
                width=(view.get("aspectRatio") or {}).get("width"),
                height=(view.get("aspectRatio") or {}).get("height"),
            )
            for view in image_views
        ]
        author = post.get("author") or {}
        return ResolvedPost(
            site=self.site,
            canonical_url=f"https://bsky.app/profile/{handle}/post/{rkey}",
            images=images,
            artist_name=author.get("displayName") or author.get("handle"),
            artist_id=author.get("handle"),
        )

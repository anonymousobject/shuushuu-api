"""Twitter/X resolver via the third-party fxtwitter API.

fxtwitter is the acknowledged weak point of the allowlist (design doc §2);
it lives behind the Resolver interface so it is swappable. Failures degrade
to a clear 'unavailable' error, never to a broken upload.
"""

import re

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
    fetch_json,
)

_URL_RE = re.compile(r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/status/(\d+)")


def _variant(photo_url: str, name: str) -> str:
    separator = "&" if "?" in photo_url else "?"
    return f"{photo_url}{separator}name={name}"


class TwitterResolver:
    site = "twitter"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        user, status_id = match.group(1), match.group(2)
        data = await fetch_json(
            client, f"https://api.fxtwitter.com/{user}/status/{status_id}", site=self.site
        )
        code = data.get("code")
        if code == 404:
            raise PostNotFoundError("tweet not found")
        if code != 200:
            raise UpstreamError(f"twitter resolution unavailable (fxtwitter code {code})")
        tweet = data.get("tweet")
        if not tweet:
            raise UpstreamError("twitter resolution unavailable (malformed fxtwitter response)")
        photos = (tweet.get("media") or {}).get("photos") or []
        if not photos:
            raise PostNotFoundError("tweet has no photos")
        images = [
            ResolvedImage(
                full_url=_variant(photo["url"], "orig"),
                thumb_url=_variant(photo["url"], "small"),
                width=photo.get("width"),
                height=photo.get("height"),
            )
            for photo in photos
        ]
        author = tweet.get("author") or {}
        return ResolvedPost(
            site=self.site,
            canonical_url=tweet.get("url") or f"https://twitter.com/{user}/status/{status_id}",
            images=images,
            artist_name=author.get("name"),
            artist_id=author.get("screen_name"),
        )

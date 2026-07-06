"""Twitter/X resolver via the third-party fxtwitter API.

fxtwitter is the acknowledged weak point of the allowlist (design doc §2);
it lives behind the Resolver interface so it is swappable. Failures degrade
to a clear 'unavailable' error, never to a broken upload.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
    fetch_json,
)

_URL_RE = re.compile(r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/status/(\d+)")


def _is_twimg_host(photo_url: str) -> bool:
    host = httpx.URL(photo_url).host or ""
    return host == "pbs.twimg.com" or host.endswith(".twimg.com")


def _variant(photo_url: str, name: str) -> str:
    # fxtwitter's photos[].url already carries its own ?name=... variant
    # (confirmed live 2026-07-06); replace it instead of appending, or
    # pbs.twimg.com 404s on the resulting duplicate `name` param.
    parts = urlsplit(photo_url)
    params = [(key, value) for key, value in parse_qsl(parts.query) if key != "name"]
    params.append(("name", name))
    query = urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


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
        if any(not _is_twimg_host(photo["url"]) for photo in photos):
            raise UpstreamError("fxtwitter returned an unexpected image host")
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

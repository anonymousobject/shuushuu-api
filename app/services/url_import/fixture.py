"""Development-only resolver so e2e can exercise the import flow end-to-end
without depending on live third-party sites."""

import re

import httpx

from app.config import settings
from app.services.url_import.base import ResolvedImage, ResolvedPost

_URL_RE = re.compile(r"^https?://urlimport-fixture\.local/post/(single|multi)$")


class FixtureResolver:
    site = "fixture"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        kind = match.group(1)
        count = 3 if kind == "multi" else 1
        base = settings.URL_IMPORT_FIXTURE_BASE_URL.rstrip("/")
        images = [
            ResolvedImage(
                full_url=f"{base}/api/v1/images/url-import-fixture/{kind}-{i}.png",
                thumb_url=f"{base}/api/v1/images/url-import-fixture/{kind}-{i}-thumb.png",
            )
            for i in range(count)
        ]
        return ResolvedPost(
            site=self.site,
            canonical_url=url,
            images=images,
            title="Fixture post",
            artist_name="Fixture Artist",
        )

"""Ko-fi resolver via OpenGraph tags on gallery/post pages."""

import re

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
    host_allowed,
)
from app.services.url_import.og import fetch_og_page

_URL_RE = re.compile(r"^https?://(?:www\.)?ko-fi\.com/(i|post)/([A-Za-z0-9_-]+)")


def _is_kofi_host(image_url: str) -> bool:
    return host_allowed(image_url, "ko-fi.com")


class KofiResolver:
    site = "ko-fi"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None  # caller guarantees match()
        kind, slug = match.group(1), match.group(2)
        canonical = f"https://ko-fi.com/{kind}/{slug}"
        tags = await fetch_og_page(
            client,
            canonical,
            site=self.site,
            allowed_hosts={"ko-fi.com", "www.ko-fi.com"},
        )
        image_url = tags.get("image")
        if not image_url:
            raise PostNotFoundError("ko-fi post has no image")
        if not _is_kofi_host(image_url):
            raise UpstreamError("ko-fi og:image points off-site")
        return ResolvedPost(
            site=self.site,
            canonical_url=canonical,
            images=[ResolvedImage(full_url=image_url)],
            title=tags.get("title"),
        )

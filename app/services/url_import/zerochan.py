"""Zerochan resolver via OpenGraph tags on the entry page."""

import re

import httpx

from app.services.url_import.base import (
    TOOL_USER_AGENT,
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
)
from app.services.url_import.og import fetch_og_page

_URL_RE = re.compile(r"^https?://(?:www\.)?zerochan\.net/(\d+)")


def _is_zerochan_host(image_url: str) -> bool:
    host = httpx.URL(image_url).host or ""
    return host == "zerochan.net" or host.endswith(".zerochan.net")


class ZerochanResolver:
    site = "zerochan"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        entry_id = match.group(1)
        canonical = f"https://www.zerochan.net/{entry_id}"
        # zerochan 503s the browser UA but returns 200 for a self-identifying
        # tool UA, per their API terms; the static image host may filter the
        # same way, so the resolved image carries the same header.
        tags = await fetch_og_page(
            client,
            canonical,
            site=self.site,
            allowed_hosts={"www.zerochan.net", "zerochan.net"},
            user_agent=TOOL_USER_AGENT,
        )
        image_url = tags.get("image")
        if not image_url:
            raise PostNotFoundError("zerochan entry has no image")
        if not _is_zerochan_host(image_url):
            raise UpstreamError("zerochan og:image points off-site")
        return ResolvedPost(
            site=self.site,
            canonical_url=canonical,
            images=[
                ResolvedImage(
                    full_url=image_url,
                    headers={"User-Agent": TOOL_USER_AGENT},
                )
            ],
            title=tags.get("title"),
        )

"""Zerochan resolver via the entry's sanctioned ?json endpoint.

The HTML page's og:image lies about the file extension for PNG entries
(always claims .jpg), so we use the JSON API instead, which also provides
dimensions, a thumbnail, and upstream provenance. Zerochan's API terms ask
for a self-identifying User-Agent naming the project and username —
anonymous projects risk bans — so ZEROCHAN_USERNAME should be configured.
"""

import re

import httpx

from app.config import settings
from app.services.url_import.base import (
    TOOL_USER_AGENT,
    ResolvedImage,
    ResolvedPost,
    UpstreamError,
    fetch_json,
    host_allowed,
    source_or,
)

_URL_RE = re.compile(r"^https?://(?:www\.)?zerochan\.net/(\d+)")


def _user_agent() -> str:
    """Zerochan's documented UA convention is "project name - username"."""
    if settings.ZEROCHAN_USERNAME:
        return f"shuushuu-url-import/1.0 - {settings.ZEROCHAN_USERNAME}"
    return TOOL_USER_AGENT


class ZerochanResolver:
    site = "zerochan"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        entry_id = match.group(1)
        entry_url = f"https://www.zerochan.net/{entry_id}"
        user_agent = _user_agent()
        data = await fetch_json(
            client,
            f"{entry_url}?json",
            site=self.site,
            headers={"User-Agent": user_agent},
        )
        full_url = data.get("full")
        if not full_url:
            raise UpstreamError("zerochan response missing expected fields")
        thumb_url = data.get("large") or data.get("small")
        if not host_allowed(full_url, "zerochan.net") or (
            thumb_url is not None and not host_allowed(thumb_url, "zerochan.net")
        ):
            raise UpstreamError("zerochan returned an unexpected image host")
        return ResolvedPost(
            site=self.site,
            canonical_url=source_or(entry_url, data.get("source")),
            images=[
                ResolvedImage(
                    full_url=full_url,
                    thumb_url=data.get("large") or data.get("small"),
                    width=data.get("width"),
                    height=data.get("height"),
                    headers={"User-Agent": user_agent},
                )
            ],
            title=data.get("primary"),
        )

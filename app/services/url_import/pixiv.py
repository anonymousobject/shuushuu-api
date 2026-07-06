"""Pixiv resolver: public (all-ages) artworks via the anonymous ajax API.

The i.pximg.net CDN gates on a pixiv Referer header, not on cookies —
spike-verified 2026-07-06 (see the design doc).
"""

import re

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    RestrictedContentError,
    fetch_json,
)

_URL_RE = re.compile(r"^https?://(?:www\.)?pixiv\.net/(?:[a-z]{2}/)?artworks/(\d+)")
_REFERER = {"Referer": "https://www.pixiv.net/"}


class PixivResolver:
    site = "pixiv"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None  # caller guarantees match()
        illust_id = match.group(1)
        data = await fetch_json(
            client,
            f"https://www.pixiv.net/ajax/illust/{illust_id}?lang=en",
            site=self.site,
            headers=_REFERER,
        )
        if data.get("error"):
            raise PostNotFoundError(data.get("message") or "pixiv artwork not available")
        body = data["body"]
        if body.get("xRestrict", 0) != 0:
            raise RestrictedContentError("Restricted (R-18) pixiv works cannot be imported")
        if body.get("illustType") == 2:
            raise RestrictedContentError("Ugoira (animated) pixiv works cannot be imported")

        if body.get("pageCount", 1) > 1:
            pages = await fetch_json(
                client,
                f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=en",
                site=self.site,
                headers=_REFERER,
            )
            images = [
                ResolvedImage(
                    full_url=page["urls"]["original"],
                    thumb_url=page["urls"].get("small"),
                    width=page.get("width"),
                    height=page.get("height"),
                    headers=dict(_REFERER),
                )
                for page in pages["body"]
            ]
        else:
            images = [
                ResolvedImage(
                    full_url=body["urls"]["original"],
                    thumb_url=body["urls"].get("small"),
                    width=body.get("width"),
                    height=body.get("height"),
                    headers=dict(_REFERER),
                )
            ]
        return ResolvedPost(
            site=self.site,
            canonical_url=f"https://www.pixiv.net/artworks/{illust_id}",
            images=images,
            title=body.get("title"),
            artist_name=body.get("userName"),
            artist_id=str(body["userId"]) if body.get("userId") else None,
        )

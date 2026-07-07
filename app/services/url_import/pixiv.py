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
    UpstreamError,
    fetch_json,
    host_allowed,
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
        body = data.get("body")
        if not body:
            raise UpstreamError("pixiv response missing expected fields")
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
            if pages.get("error"):
                raise PostNotFoundError(pages.get("message") or "pixiv artwork not available")
            images = []
            for page in pages.get("body") or []:
                urls = page.get("urls") or {}
                original = urls.get("original")
                if not original:
                    raise UpstreamError("pixiv response missing expected fields")
                images.append(
                    ResolvedImage(
                        full_url=original,
                        thumb_url=urls.get("small"),
                        width=page.get("width"),
                        height=page.get("height"),
                        headers=dict(_REFERER),
                    )
                )
        else:
            urls = body.get("urls") or {}
            original = urls.get("original")
            if not original:
                raise UpstreamError("pixiv response missing expected fields")
            images = [
                ResolvedImage(
                    full_url=original,
                    thumb_url=urls.get("small"),
                    width=body.get("width"),
                    height=body.get("height"),
                    headers=dict(_REFERER),
                )
            ]
        for image in images:
            if not host_allowed(image.full_url, "pximg.net") or (
                image.thumb_url is not None and not host_allowed(image.thumb_url, "pximg.net")
            ):
                raise UpstreamError("pixiv returned an unexpected image host")
        return ResolvedPost(
            site=self.site,
            canonical_url=f"https://www.pixiv.net/artworks/{illust_id}",
            images=images,
            title=body.get("title"),
            artist_name=body.get("userName"),
            artist_id=str(body["userId"]) if body.get("userId") else None,
        )

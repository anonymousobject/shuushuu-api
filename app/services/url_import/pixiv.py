"""Pixiv resolver: public (all-ages) artworks via the anonymous ajax API.

The i.pximg.net CDN gates on a pixiv Referer header, not on cookies —
spike-verified 2026-07-06 (see the design doc).
"""

import re
from typing import Any

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

# pixiv's "sanity level"; 4 and up is flagged sensitive (but not R-18 — that is
# xRestrict). Verified 2026-08-01 against 12 works that failed in production.
_SENSITIVE_SANITY_LEVEL = 4


def _is_login_gated(body: dict[str, Any]) -> bool:
    """True if pixiv withheld this work's image URLs pending a login.

    For works behind the login gate the ajax API still answers 200 with
    error:false and a full metadata body, but every value in `urls` is null.
    Two conditions trigger it, neither of which sets xRestrict, so the R-18
    check misses both: the artist restricting the work to logged-in viewers,
    and pixiv flagging it sensitive (sl >= 4).
    """
    return bool(body.get("isLoginOnly")) or (body.get("sl") or 0) >= _SENSITIVE_SANITY_LEVEL


class PixivResolver:
    site = "pixiv"
    example_url: str | None = "https://www.pixiv.net/artworks/12345678"

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
        # Checked before the pageCount branch: pixiv nulls the whole `urls` block
        # on the illust body, which carries p0 even for multi-page works — so this
        # covers both shapes and spares a pointless /pages round-trip.
        if not (body.get("urls") or {}).get("original") and _is_login_gated(body):
            raise RestrictedContentError(
                "This Pixiv URL is only viewable while logged in to Pixiv. "
                "Save the image and upload it manually, or paste a mirror URL "
                "(danbooru, gelbooru, zerochan) if one exists."
            )

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

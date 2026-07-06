"""Moebooru resolver (yande.re; the classic post.json API)."""

import re

import httpx

from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    fetch_json,
    source_or,
)

_URL_RE = re.compile(r"^https?://yande\.re/post/show/(\d+)")


class MoebooruResolver:
    site = "yande.re"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        post_id = match.group(1)
        data = await fetch_json(
            client, f"https://yande.re/post.json?tags=id:{post_id}", site=self.site
        )
        if not data:
            raise PostNotFoundError("yande.re post not found")
        post = data[0]
        post_url = f"https://yande.re/post/show/{post_id}"
        return ResolvedPost(
            site=self.site,
            canonical_url=source_or(post_url, post.get("source")),
            images=[
                ResolvedImage(
                    full_url=post["file_url"],
                    thumb_url=post.get("preview_url"),
                    width=post.get("width"),
                    height=post.get("height"),
                )
            ],
        )

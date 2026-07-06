"""Gelbooru resolver (dapi JSON API).

Gelbooru's dapi returns HTTP 401 without an api_key/user_id pair, so the
registry (registry.py) only advertises this resolver when both are configured
(app.config.settings.GELBOORU_API_KEY / GELBOORU_USER_ID).
"""

from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.services.url_import.base import (
    PostNotFoundError,
    ResolvedImage,
    ResolvedPost,
    fetch_json,
    source_or,
)


class GelbooruResolver:
    site = "gelbooru"

    def _post_id(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.hostname not in ("gelbooru.com", "www.gelbooru.com"):
            return None
        params = parse_qs(parsed.query)
        if params.get("page") != ["post"] or params.get("s") != ["view"]:
            return None
        post_id = params.get("id", [""])[0]
        return post_id if post_id.isdigit() else None

    def match(self, url: str) -> bool:
        return self._post_id(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        post_id = self._post_id(url)
        assert post_id is not None
        dapi_url = f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&id={post_id}"
        if settings.GELBOORU_API_KEY and settings.GELBOORU_USER_ID:
            dapi_url += f"&api_key={settings.GELBOORU_API_KEY}&user_id={settings.GELBOORU_USER_ID}"
        data = await fetch_json(client, dapi_url, site=self.site)
        posts = data.get("post") or []
        if not posts:
            raise PostNotFoundError("gelbooru post not found")
        post = posts[0]
        post_url = f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
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

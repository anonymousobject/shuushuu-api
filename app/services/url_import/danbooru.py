"""Danbooru resolver (official JSON API)."""

import re

import httpx

from app.config import settings
from app.services.url_import.base import (
    ResolvedImage,
    ResolvedPost,
    RestrictedContentError,
    fetch_json,
    source_or,
)

_URL_RE = re.compile(r"^https?://danbooru\.donmai\.us/posts/(\d+)")


class DanbooruResolver:
    site = "danbooru"

    def match(self, url: str) -> bool:
        return _URL_RE.match(url) is not None

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost:
        match = _URL_RE.match(url)
        assert match is not None
        post_id = match.group(1)
        post_url = f"https://danbooru.donmai.us/posts/{post_id}"
        json_url = f"{post_url}.json"
        if settings.DANBOORU_LOGIN and settings.DANBOORU_API_KEY:
            json_url += f"?login={settings.DANBOORU_LOGIN}&api_key={settings.DANBOORU_API_KEY}"
        data = await fetch_json(client, json_url, site=self.site)
        file_url = data.get("file_url")
        if not file_url:
            raise RestrictedContentError("danbooru post has no publicly accessible file")
        artist = (data.get("tag_string_artist") or "").split(" ")[0]
        return ResolvedPost(
            site=self.site,
            canonical_url=source_or(post_url, data.get("source")),
            images=[
                ResolvedImage(
                    full_url=file_url,
                    thumb_url=data.get("preview_file_url"),
                    width=data.get("image_width"),
                    height=data.get("image_height"),
                )
            ],
            artist_name=artist.replace("_", " ") if artist else None,
        )

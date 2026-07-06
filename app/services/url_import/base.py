"""Shared types and helpers for the URL-import resolver framework.

Resolvers pattern-match an external post URL, extract an ID, and construct
their own requests against pinned hostnames — user-provided URLs are never
fetched directly (see docs/plans/2026-07-06-url-import-design.md).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class UrlImportError(Exception):
    """Base error for URL-import failures."""


class UnsupportedUrlError(UrlImportError):
    """No resolver matches the URL."""


class PostNotFoundError(UrlImportError):
    """The post does not exist upstream."""


class RestrictedContentError(UrlImportError):
    """The post exists but cannot be imported (R-18, ugoira, login-gated)."""


class UpstreamError(UrlImportError):
    """The upstream site failed or returned something unusable."""


@dataclass
class ResolvedImage:
    full_url: str
    thumb_url: str | None = None
    width: int | None = None
    height: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedPost:
    site: str
    canonical_url: str
    images: list[ResolvedImage]
    title: str | None = None
    artist_name: str | None = None
    artist_id: str | None = None


class Resolver(Protocol):
    site: str

    def match(self, url: str) -> bool: ...

    async def resolve(self, url: str, client: httpx.AsyncClient) -> ResolvedPost: ...


def source_or(default_url: str, source: str | None) -> str:
    """Prefer a post's upstream http(s) source as provenance; else the post URL."""
    if source and source.startswith(("http://", "https://")):
        return source
    return default_url


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    site: str,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET a JSON endpoint with a browser UA, mapping failures to import errors."""
    request_headers = {"User-Agent": BROWSER_USER_AGENT}
    if headers:
        request_headers.update(headers)
    try:
        response = await client.get(url, headers=request_headers)
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{site} request failed") from exc
    if response.status_code == 404:
        raise PostNotFoundError(f"{site} post not found")
    if response.status_code != 200:
        raise UpstreamError(f"{site} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(f"{site} returned invalid JSON") from exc

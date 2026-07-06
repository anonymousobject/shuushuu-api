"""Minimal OpenGraph tag extraction for allowlisted HTML pages.

Regex-based on purpose: we only need og:image / og:title from two known
sites, not a general HTML parser dependency.
"""

import html
import re

import httpx

from app.services.url_import.base import (
    BROWSER_USER_AGENT,
    PostNotFoundError,
    UpstreamError,
)

_MAX_REDIRECTS = 3

_OG_PROP_FIRST = re.compile(
    r'<meta[^>]+property=["\']og:([\w:]+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_OG_CONTENT_FIRST = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:([\w:]+)["\']',
    re.IGNORECASE,
)


def extract_og_tags(page_html: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for match in _OG_PROP_FIRST.finditer(page_html):
        tags.setdefault(match.group(1).lower(), html.unescape(match.group(2)))
    for match in _OG_CONTENT_FIRST.finditer(page_html):
        tags.setdefault(match.group(2).lower(), html.unescape(match.group(1)))
    return tags


async def fetch_og_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    site: str,
    allowed_hosts: set[str],
) -> dict[str, str]:
    """Fetch an allowlisted page and return its OG tags; refuse off-host redirects."""
    current = url
    for _ in range(_MAX_REDIRECTS):
        try:
            response = await client.get(current, headers={"User-Agent": BROWSER_USER_AGENT})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{site} request failed") from exc
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            next_url = str(httpx.URL(current).join(location))
            if httpx.URL(next_url).host not in allowed_hosts:
                raise UpstreamError(f"{site} redirected off-host")
            current = next_url
            continue
        if response.status_code == 404:
            raise PostNotFoundError(f"{site} post not found")
        if response.status_code != 200:
            raise UpstreamError(f"{site} returned HTTP {response.status_code}")
        return extract_og_tags(response.text)
    raise UpstreamError(f"{site} redirect loop")

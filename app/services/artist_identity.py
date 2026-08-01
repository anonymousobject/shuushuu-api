"""Parse artist-identity URLs and search queries into (site, external_id) pairs.

v1 registers pixiv only (POC). To add a site, append its URL patterns and
site constant — see docs/plans/2026-08-01-external-artist-identity-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks

SITE_PIXIV = "pixiv"

# Every pixiv profile-URL form in the wild. Old descs/links use the legacy
# member.php forms; modern URLs may carry a language prefix (/en/users/...).
_PIXIV_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.|touch\.)?pixiv\.net/(?:[a-z]{2}/)?users/(\d+)", re.IGNORECASE),
    re.compile(
        r"^https?://(?:www\.|touch\.)?pixiv\.net/member(?:_illust)?\.php\?(?:[^#]*&)?id=(\d+)",
        re.IGNORECASE,
    ),
]

_BARE_ID = re.compile(r"^\d{1,12}$")
_PIXIV_PREFIXED = re.compile(r"^pixiv[\s:]+(\d{1,12})$", re.IGNORECASE)


@dataclass(frozen=True)
class ArtistIdentity:
    site: str
    external_id: str


def parse_identity_url(url: str) -> ArtistIdentity | None:
    """Return the identity a profile URL encodes, or None for any other URL."""
    stripped = url.strip()
    for pattern in _PIXIV_URL_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return ArtistIdentity(site=SITE_PIXIV, external_id=match.group(1))
    return None


def parse_identity_query(q: str) -> ArtistIdentity | None:
    """Return the identity a search query names: bare ID, 'pixiv <id>', or URL."""
    stripped = q.strip()
    if not stripped:
        return None
    if _BARE_ID.match(stripped):
        return ArtistIdentity(site=SITE_PIXIV, external_id=stripped)
    prefixed = _PIXIV_PREFIXED.match(stripped)
    if prefixed:
        return ArtistIdentity(site=SITE_PIXIV, external_id=prefixed.group(1))
    return parse_identity_url(stripped)


def canonical_profile_url(identity: ArtistIdentity) -> str:
    """The URL to create when backfilling an identity that has no link yet."""
    return f"https://www.pixiv.net/users/{identity.external_id}"


async def resolve_identity(db: AsyncSession, identity: ArtistIdentity) -> Tags | None:
    """Exact lookup: which tag owns this (site, external_id)? None if unclaimed."""
    result = await db.execute(
        select(Tags)
        .join(TagExternalLinks, TagExternalLinks.tag_id == Tags.tag_id)  # type: ignore[arg-type]
        .where(TagExternalLinks.site == identity.site)  # type: ignore[arg-type]
        .where(TagExternalLinks.external_id == identity.external_id)  # type: ignore[arg-type]
        .limit(1)
    )
    return result.scalars().first()

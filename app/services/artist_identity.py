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

# The stored `site` column value is always lowercase (used for lookups and a
# future UNIQUE(site, external_id) index); this maps it to the case mods
# actually want shown -- they capitalize "Pixiv" everywhere.
SITE_DISPLAY_NAMES = {SITE_PIXIV: "Pixiv"}


def site_display_name(site: str) -> str:
    """The display form of a site name, falling back to the raw value for any
    site without a registered mapping."""
    return SITE_DISPLAY_NAMES.get(site, site)


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

# Non-anchored form of the URL patterns above, for scanning free-text desc
# content rather than validating a whole field. Shared by the backfill (reads
# desc, leaves it alone) and the desc mover (reads desc, then strips it).
DESC_URL_PATTERN = re.compile(
    r"https?://(?:www\.|touch\.)?pixiv\.net/"
    r"(?:(?:[a-z]{2}/)?users/(\d+)|member(?:_illust)?\.php\?(?:[^\s#]*&)?id=(\d+))",
    re.IGNORECASE,
)
# Bare "pixiv 97567" / "pixiv #97567" / "pixiv: 97567" text in a desc, with no
# URL involved. Minimum 4 digits to avoid matching prose like "pixiv 100
# followers". The literal "." in "pixiv.net" immediately after "pixiv" is not
# in the [\s#:] class, so this never re-matches ids already handled by
# DESC_URL_PATTERN (verified with a dedicated test rather than assumed).
DESC_BARE_ID_PATTERN = re.compile(r"\bpixiv[\s#:]*(\d{4,12})\b", re.IGNORECASE)


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
    if identity.site != SITE_PIXIV:
        raise ValueError(f"No canonical profile URL scheme registered for site {identity.site!r}")
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

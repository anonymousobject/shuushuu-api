"""Backfill (site, external_id) identity from links, alias titles, and descs.

Three sources in confidence order; see the design doc §5. Dry-run by default —
`apply=False` computes the full report without writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity import (
    SITE_PIXIV,
    ArtistIdentity,
    canonical_profile_url,
    parse_identity_url,
)

_ALIAS_TITLE = re.compile(r"^pixiv\s+(\d+)$", re.IGNORECASE)
# Non-anchored forms of the URL patterns, for scanning desc text.
_DESC_URL = re.compile(
    r"https?://(?:www\.|touch\.)?pixiv\.net/"
    r"(?:(?:[a-z]{2}/)?users/(\d+)|member(?:_illust)?\.php\?(?:[^\s#]*&)?id=(\d+))",
    re.IGNORECASE,
)


@dataclass
class BackfillReport:
    links_parsed: int = 0
    links_created_from_aliases: int = 0
    links_created_from_desc: int = 0
    artist_tags_without_identity: int = 0
    anomalies: list[str] = field(default_factory=list)


async def _identity_owners(db: AsyncSession) -> dict[tuple[str, str], int]:
    """Map (site, external_id) -> owning tag_id for every populated link."""
    rows = await db.execute(
        select(  # type: ignore[call-overload]
            TagExternalLinks.tag_id, TagExternalLinks.site, TagExternalLinks.external_id
        ).where(TagExternalLinks.site.is_not(None))  # type: ignore[union-attr]
    )
    return {(r.site, r.external_id): r.tag_id for r in rows.all()}


async def run_backfill(db: AsyncSession, *, apply: bool) -> BackfillReport:
    report = BackfillReport()

    # --- Source 1: parse existing link URLs in place ---
    links = (
        (
            await db.execute(
                select(TagExternalLinks).where(TagExternalLinks.site.is_(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    owners = await _identity_owners(db)
    for link in links:
        identity = parse_identity_url(link.url)
        if identity is None:
            continue
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner is not None and owner != link.tag_id:
            report.anomalies.append(
                f"link {link.link_id} (tag {link.tag_id}): {identity.site} "
                f"{identity.external_id} already owned by tag {owner}"
            )
            continue
        if owner == link.tag_id:
            report.anomalies.append(
                f"link {link.link_id} (tag {link.tag_id}): duplicate identity "
                f"{identity.site} {identity.external_id} — tag already owns it "
                "via another link"
            )
            continue
        report.links_parsed += 1
        owners[key] = link.tag_id
        if apply:
            link.site = identity.site
            link.external_id = identity.external_id

    # --- Source 2: "Pixiv <id>" alias titles ---
    aliases = (
        (
            await db.execute(
                select(Tags).where(Tags.alias_of.is_not(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    for alias in aliases:
        if alias.title is None:
            continue
        match = _ALIAS_TITLE.match(alias.title)
        if not match:
            continue
        identity = ArtistIdentity(site=SITE_PIXIV, external_id=match.group(1))
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner == alias.alias_of:
            continue  # canonical already has it
        if owner is not None:
            report.anomalies.append(
                f"alias tag {alias.tag_id} '{alias.title}' -> tag {alias.alias_of}, "
                f"but identity owned by tag {owner}"
            )
            continue
        report.links_created_from_aliases += 1
        owners[key] = alias.alias_of  # type: ignore[assignment]
        if apply:
            db.add(
                TagExternalLinks(
                    tag_id=alias.alias_of,
                    url=canonical_profile_url(identity),
                    site=identity.site,
                    external_id=identity.external_id,
                )
            )

    # --- Source 3: pixiv URLs embedded in artist descs ---
    artists = (
        (
            await db.execute(
                select(Tags)
                .where(Tags.type == TagType.ARTIST)  # type: ignore[arg-type]
                .where(Tags.alias_of.is_(None))  # type: ignore[union-attr]
                .where(Tags.desc.is_not(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    claimed_tags = {tag_id for (_site, _eid), tag_id in owners.items()}
    for artist in artists:
        if artist.tag_id in claimed_tags:
            continue
        ids = {m.group(1) or m.group(2) for m in _DESC_URL.finditer(artist.desc or "")}
        if not ids:
            continue
        if len(ids) > 1:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': desc names multiple pixiv ids {sorted(ids)}"
            )
            continue
        identity = ArtistIdentity(site=SITE_PIXIV, external_id=ids.pop())
        key = (identity.site, identity.external_id)
        owner = owners.get(key)
        if owner is not None:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': desc pixiv id "
                f"{identity.external_id} already owned by tag {owner}"
            )
            continue
        report.links_created_from_desc += 1
        owners[key] = artist.tag_id  # type: ignore[assignment]
        if apply:
            db.add(
                TagExternalLinks(
                    tag_id=artist.tag_id,
                    url=canonical_profile_url(identity),
                    site=identity.site,
                    external_id=identity.external_id,
                )
            )

    # --- Coverage figure for the report ---
    claimed_tags = {tag_id for (_s, _e), tag_id in owners.items()}
    all_artists = (
        (
            await db.execute(
                select(Tags.tag_id)  # type: ignore[call-overload]
                .where(Tags.type == TagType.ARTIST)
                .where(Tags.alias_of.is_(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    report.artist_tags_without_identity = len([t for t in all_artists if t not in claimed_tags])

    if apply:
        await db.commit()
    return report

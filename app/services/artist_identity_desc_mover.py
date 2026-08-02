"""Move pixiv-identity text out of artist descs and into the owning link.

The backfill (`artist_identity_backfill.py`) is additive only: it copies
identity out of aliases, titles, and descs into `tag_external_links` rows but
never touches the source desc text, so older artists show the pixiv URL/id
twice (desc + link list). Mods asked for this explicitly ("shame if we can't
move the links"). See docs/plans/2026-08-01-external-artist-identity-design.md.

For each canonical artist tag whose desc parses to an identity the tag
already OWNS via a link:

1. **Verbatim preservation.** If the link's URL string differs from the
   desc's literal URL text, the link's URL is rewritten to that verbatim
   desc form. Old URL forms (`member.php`) may be archived where the
   canonical form the backfill generated isn't, so the exact string a mod
   actually used must survive somewhere -- the link row is the durable home
   for it. Never deletes a link: if that exact URL already exists as another
   row on the tag, the rewrite is skipped (both forms are already kept).
2. **Strip + tidy the desc.** The identity text is removed, orphaned
   `/`-or-`|` separators left dangling at the ends or doubled mid-string are
   collapsed, and whitespace is normalized. An empty result is stored as
   NULL, matching the site's "no description" convention (see
   `TagCreate.sanitize_desc`).
3. **Bare "pixiv <id>" text** (no URL) is moved the same way; nothing
   link-side changes since there's no URL to preserve.
4. **Anything ambiguous is an anomaly, not a guess:** the desc's identity
   isn't owned by this tag, the desc names/mentions more than one pixiv
   occurrence, or the stripped remainder doesn't look confidently clean (a
   leftover separator we couldn't merge, or a dangling label like
   "profile:"). No write happens for an anomaly.

Dry-run by default; `apply=True` writes. Counts and the before/after
`samples` list are always computed (even dry-run) so the caller can render a
review report before committing to `--apply`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagAuditActionType, TagType
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_external_link import TagExternalLinks
from app.services.artist_identity import (
    DESC_BARE_ID_PATTERN,
    DESC_URL_PATTERN,
    SITE_PIXIV,
    ArtistIdentity,
)

# Punctuation that looks like an orphaned connector/label when left at the
# very start or end of a tidied desc (e.g. "profile:", "see -", "-, "). Only
# "/" and "|" are documented separator conventions we know how to merge
# confidently (handled structurally below); anything else surviving at a
# boundary is flagged rather than guessed away.
_BOUNDARY_REMNANT_CHARS = ":/|,-;"


class _AmbiguousRemnant(Exception):
    """Raised when stripping the identity text would leave a desc remainder
    that doesn't look confidently clean. Conservative by design -- a human
    should look at these rather than have the mover guess."""


def _strip_and_tidy(desc: str, start: int, end: int) -> str | None:
    """Remove desc[start:end] and tidy what's left.

    Collapses a same-character '/' or '|' separator left dangling at either
    end of the desc, or doubled mid-string (both sides of the removed span
    had one), then normalizes whitespace. Returns None if nothing is left
    (caller stores NULL). Raises `_AmbiguousRemnant` if the result still
    looks broken.
    """
    remaining = desc[:start] + desc[end:]

    # A same-character separator left adjacent by the removal, e.g.
    # "name / <removed> / more" -> "name /  / more" -> "name / more".
    remaining = re.sub(r"[ \t]*/[ \t]*/[ \t]*", " / ", remaining)
    remaining = re.sub(r"[ \t]*\|[ \t]*\|[ \t]*", " | ", remaining)

    # A separator left dangling at either end, e.g. "name / " or " / name".
    remaining = re.sub(r"^[ \t]*[/|][ \t]*", "", remaining)
    remaining = re.sub(r"[ \t]*[/|][ \t]*$", "", remaining)

    # Collapse the whitespace hole the removal itself may have left
    # mid-string (bare-text removal from prose has no separator at all).
    remaining = re.sub(r"\s+", " ", remaining).strip()

    if not remaining:
        return None

    mixed_doubled_separator = re.search(r"[/|]\s*[/|]", remaining)
    if (
        mixed_doubled_separator
        or remaining[0] in _BOUNDARY_REMNANT_CHARS
        or remaining[-1] in _BOUNDARY_REMNANT_CHARS
    ):
        raise _AmbiguousRemnant(remaining)

    return remaining


@dataclass
class DescMoveSample:
    """A before/after pair for the dry-run review report."""

    tag_id: int
    title: str | None
    before: str
    after: str | None


@dataclass
class DescMoverReport:
    descs_cleaned: int = 0  # desc had a pixiv URL, now stripped
    bare_text_stripped: int = 0  # desc had bare "pixiv <id>" text, now stripped
    descs_emptied: int = 0  # subset of the above two where the result is NULL
    links_rewritten_to_verbatim: int = 0
    links_verbatim_skipped_duplicate: int = 0
    anomalies: list[str] = field(default_factory=list)
    samples: list[DescMoveSample] = field(default_factory=list)


async def _find_owning_link(
    db: AsyncSession, *, tag_id: int, identity: ArtistIdentity
) -> tuple[TagExternalLinks | None, int | None]:
    """The tag's own link for this identity, plus the tag_id of whoever else
    owns it (if it isn't this tag) for a more useful anomaly message."""
    rows = (
        (
            await db.execute(
                select(TagExternalLinks).where(
                    TagExternalLinks.site == identity.site,  # type: ignore[arg-type]
                    TagExternalLinks.external_id == identity.external_id,  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    own_link = next((row for row in rows if row.tag_id == tag_id), None)
    other_owner = next((row.tag_id for row in rows if row.tag_id != tag_id), None)
    return own_link, other_owner


async def run_desc_mover(db: AsyncSession, *, apply: bool) -> DescMoverReport:
    report = DescMoverReport()

    canonical_artists = (
        (
            await db.execute(
                select(Tags)
                .where(Tags.type == TagType.ARTIST)  # type: ignore[arg-type]
                .where(Tags.alias_of.is_(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )

    for artist in canonical_artists:
        if not artist.desc:
            continue

        url_matches = [(m, "url") for m in DESC_URL_PATTERN.finditer(artist.desc)]
        bare_matches = [(m, "bare") for m in DESC_BARE_ID_PATTERN.finditer(artist.desc)]
        all_matches = url_matches + bare_matches
        if not all_matches:
            continue
        if len(all_matches) > 1:
            ids = sorted(
                {
                    (m.group(1) or m.group(2)) if kind == "url" else m.group(1)
                    for m, kind in all_matches
                }
            )
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': desc has more than one pixiv "
                f"mention ({', '.join(ids)}) -- ambiguous which occurrence to move, skipping"
            )
            continue

        match, kind = all_matches[0]
        external_id = (match.group(1) or match.group(2)) if kind == "url" else match.group(1)
        identity = ArtistIdentity(site=SITE_PIXIV, external_id=external_id)

        own_link, other_owner = await _find_owning_link(
            db,
            tag_id=artist.tag_id,  # type: ignore[arg-type]
            identity=identity,
        )
        if own_link is None:
            if other_owner is not None:
                report.anomalies.append(
                    f"tag {artist.tag_id} '{artist.title}': desc pixiv id "
                    f"{identity.external_id} is owned by tag {other_owner}, not this tag"
                )
            else:
                report.anomalies.append(
                    f"tag {artist.tag_id} '{artist.title}': desc pixiv id "
                    f"{identity.external_id} has no owning link on any tag"
                )
            continue

        try:
            new_desc = _strip_and_tidy(artist.desc, match.start(), match.end())
        except _AmbiguousRemnant as exc:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': stripping the identity text would "
                f"leave an unclear remnant ({exc}) -- skipping"
            )
            continue

        report.samples.append(
            DescMoveSample(
                tag_id=artist.tag_id,  # type: ignore[arg-type]
                title=artist.title,
                before=artist.desc,
                after=new_desc,
            )
        )

        if kind == "url":
            report.descs_cleaned += 1
            desc_url_text = match.group(0)
            if own_link.url != desc_url_text:
                duplicate = (
                    await db.execute(
                        select(TagExternalLinks).where(
                            TagExternalLinks.tag_id == artist.tag_id,  # type: ignore[arg-type]
                            TagExternalLinks.url == desc_url_text,  # type: ignore[arg-type]
                        )
                    )
                ).first()
                if duplicate is not None:
                    report.links_verbatim_skipped_duplicate += 1
                else:
                    report.links_rewritten_to_verbatim += 1
                    if apply:
                        own_link.url = desc_url_text
        else:
            report.bare_text_stripped += 1

        if new_desc is None:
            report.descs_emptied += 1

        if apply:
            old_desc = artist.desc
            artist.desc = new_desc
            db.add(
                TagAuditLog(
                    tag_id=artist.tag_id,
                    action_type=TagAuditActionType.DESCRIPTION_CHANGE,
                    old_desc=old_desc,
                    new_desc=new_desc,
                    user_id=None,
                )
            )

    if apply:
        await db.commit()
    return report

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
2. **Strip + tidy the desc.** The full identity text is removed (a pasted
   URL's full extent, trailing query string and all -- not just the id
   digits the parser needs), orphaned `/` `|` `,` `-` separators left
   dangling at the edges or doubled by the removal are collapsed, and
   whitespace is normalized. An empty result is stored as NULL, matching the
   site's "no description" convention (see `TagCreate.sanitize_desc`). URL
   expansion beyond the id digits only ever consumes characters we're
   confident are a real continuation (`_CONFIDENT_URL_TAIL_CHARS`) -- never
   adjacent prose, and never non-ASCII text glued directly against the URL
   with no separator at all.
3. **Bare "pixiv <id>" text** (no URL) is moved the same way; nothing
   link-side changes since there's no URL to preserve.
4. **Anything ambiguous is an anomaly, not a guess:** the desc's identity
   isn't owned by this tag, the desc names/mentions more than one pixiv
   occurrence, the text right after a URL's id digits isn't confidently more
   URL, or the remainder touching the removed span doesn't look confidently
   clean -- a label character immediately adjacent ("profile:", "see;"), an
   emptied bracket pair ("()"), mismatched delimiters on either side, or text
   glued directly to the removed span on *both* sides with no whitespace
   anywhere nearby (joining would guess a separator that was never there).
   This is all checked locally around the removed span, not just at the
   whole desc's start/end, so a mid-string "profile:" is caught exactly like
   one at the very start. No write happens for an anomaly.

Dry-run by default; `apply=True` writes. Counts and the before/after
`samples` list (which also carries any pending link-URL rewrite, so the
review report covers both sides of a change) are always computed (even
dry-run) so the caller can render a review report before committing to
`--apply`.
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

# Characters that separate two independent chunks of desc text. A single one
# of these left mid-string with real content on both sides is left alone (it
# was probably always doing its job as a separator); doubled by the removal
# (the same character on both sides of the removed span) it's confidently
# collapsed to one; dangling at a true edge (nothing on the other side) it's
# confidently trimmed.
_DELIMITER_CHARS = frozenset("/|,-")
# Label/connector punctuation that implies something follows or precedes it
# ("profile:", "see;") -- never auto-resolved. Whether the intended fix drops
# the label entirely, replaces it, or joins the surrounding text with or
# without a space isn't something we can guess confidently, so any of these
# touching the removed span (on either side, at any position in the desc)
# is flagged.
_LABEL_CHARS = frozenset(":;")
# Boundary characters that end a URL token pasted into free text -- these
# (plus any non-ASCII character, checked separately) stop the expansion walk
# in _expand_url_span.
_URL_TOKEN_STOP_CHARS = frozenset(" \t\r\n\"'()<>[]{}")
# Trailing punctuation that is overwhelmingly a sentence/clause ending rather
# than intentionally part of a pasted URL -- the standard "linkifier"
# convention (auto-link tools exclude these from a URL's trailing edge even
# though the URL spec technically permits them).
_TRAILING_PROSE_PUNCTUATION = ".,;:!?"
# Characters we're confident belong to a real trailing query-string/path
# continuation (e.g. "&ref=abc") rather than adjacent prose that happens to
# be URL-legal. Deliberately tight: excludes punctuation (",", ";", ":", "!",
# "?", "'", "@", "*", "$", ...) that's technically valid in a URL per RFC
# 3986 but is far more often just prose touching the URL in this dataset's
# informal artist-bio descs. Never widen this to "guess more" -- if the tail
# beyond the id-focused regex match isn't made entirely of these characters,
# _expand_url_span reports it as unconfident and the caller flags an anomaly
# instead of persisting a guess.
_CONFIDENT_URL_TAIL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789&=%_.~-"
)


class _AmbiguousRemnant(Exception):
    """Raised when stripping the identity text would leave a desc remainder
    that doesn't look confidently clean. Conservative by design -- a human
    should look at these rather than have the mover guess."""


def _expand_url_span(desc: str, match: re.Match[str]) -> tuple[int, int] | None:
    """DESC_URL_PATTERN only captures through the id digits -- that's all
    identity *parsing* needs -- but the mover has to move the FULL pasted
    URL, trailing query string and all (e.g. '&ref=abc'), so the verbatim
    link rewrite doesn't truncate it and the desc strip doesn't leave the
    tail behind as garbage.

    The pattern's start is anchored to the literal "http(s)://" scheme,
    always the true start of a pasted URL, so only the end needs extending.
    The walk stops at whitespace, a quote/bracket, or (critically) any
    non-ASCII character -- CJK text glued directly after the id digits (no
    separator at all) must never be pulled into the URL. Trailing prose
    punctuation (a sentence period, a list comma) is then trimmed off
    regardless of how far the walk reached, since a pasted URL essentially
    never legitimately *ends* in one of those right before whitespace even
    though the URL spec permits them.

    Returns None if what's left beyond the original match, after that trim,
    still isn't made entirely of characters we're confident are a real URL
    continuation -- the caller flags an anomaly rather than persisting a
    guess.
    """
    end = match.end()
    while end < len(desc):
        ch = desc[end]
        if not ch.isascii() or ch in _URL_TOKEN_STOP_CHARS:
            break
        end += 1

    trimmed_end = end
    while trimmed_end > match.end() and desc[trimmed_end - 1] in _TRAILING_PROSE_PUNCTUATION:
        trimmed_end -= 1

    tail = desc[match.end() : trimmed_end]
    if tail and not all(c in _CONFIDENT_URL_TAIL_CHARS for c in tail):
        return None

    return match.start(), trimmed_end


def _join(before: str, sep: str, after: str) -> str | None:
    if not before and not after:
        return None
    if not before:
        return after
    if not after:
        return before
    if sep == ",":
        # English convention: no space before a comma, one space after --
        # unlike "/", "|", "-" which read naturally with a space on both
        # sides ("A / B", "A - B"), "A , B" looks like a typo.
        return f"{before}, {after}"
    return f"{before} {sep} {after}"


def _strip_and_tidy(desc: str, start: int, end: int) -> str | None:
    """Remove desc[start:end] and tidy what's left.

    Looks only at the text immediately touching the removed span (not just
    the whole desc's global boundaries -- a dangling label or an orphaned
    separator is just as much a problem mid-string as at the edges).
    Confidently resolves: nothing left at all (-> None), a delimiter
    ("/|,-") doubled by the removal or dangling at a true edge, and ordinary
    prose (the whitespace gap left by the removal is closed). Raises
    `_AmbiguousRemnant` for anything else: a label character ("profile:")
    touching the span, an emptied bracket pair, mismatched delimiters on
    either side, or real content glued to the span on both sides with no
    whitespace anywhere nearby.
    """
    before = desc[:start]
    after = desc[end:]
    before_stripped = before.rstrip()
    after_stripped = after.lstrip()
    before_char = before_stripped[-1] if before_stripped else None
    after_char = after_stripped[0] if after_stripped else None

    # Nothing but whitespace on either side: the mention was the whole desc.
    if not before_stripped and not after_stripped:
        return None

    # A label/connector immediately touching the span -- never auto-resolved,
    # regardless of whether real content follows/precedes it.
    if before_char in _LABEL_CHARS or after_char in _LABEL_CHARS:
        raise _AmbiguousRemnant(f"{before_stripped!r} | {after_stripped!r}")

    # A bracket pair left wrapping nothing -- ambiguous whether collapsing it
    # should also join the outer text with or without a space.
    if before_char == "(" and after_char == ")":
        raise _AmbiguousRemnant(f"{before_stripped!r} | {after_stripped!r}")

    # Same delimiter on both sides -- the removal doubled it; collapse to one.
    if before_char is not None and before_char == after_char and before_char in _DELIMITER_CHARS:
        new_before = before_stripped[:-1].rstrip()
        new_after = after_stripped[1:].lstrip()
        return _join(new_before, before_char, new_after)

    # Different delimiters on both sides ("A / <removed> | B") -- ambiguous
    # which one to keep.
    if (
        before_char in _DELIMITER_CHARS
        and after_char in _DELIMITER_CHARS
        and before_char != after_char
    ):
        raise _AmbiguousRemnant(f"{before_stripped!r} | {after_stripped!r}")

    # A delimiter dangling at a true edge (nothing on the other side) is safe
    # to trim, e.g. "name / <removed>" with nothing following.
    if not after_stripped and before_char in _DELIMITER_CHARS:
        return before_stripped[:-1].rstrip() or None
    if not before_stripped and after_char in _DELIMITER_CHARS:
        return after_stripped[1:].lstrip() or None

    # Ordinary prose, or a lone mid-string delimiter still separating two
    # real chunks (left as-is) -- just close the whitespace gap.
    if not before_stripped:
        return after_stripped
    if not after_stripped:
        return before_stripped

    # The removed text was glued directly to real content on *both* sides --
    # no whitespace anywhere nearby at all (e.g. CJK prose immediately
    # abutting a pasted URL on both ends). Joining with a space would guess
    # at a separator convention that clearly wasn't there; gluing the two
    # sides together would guess the opposite. Flag rather than pick.
    if before == before_stripped and after == after_stripped:
        raise _AmbiguousRemnant(f"{before_stripped!r} | {after_stripped!r}")

    return f"{before_stripped} {after_stripped}"


@dataclass
class DescMoveSample:
    """A before/after pair for the dry-run review report.

    Covers both the desc text and, when a verbatim link-URL rewrite is
    pending (link_url_before is not None), the link's URL -- mods reviewing
    the report before --apply need to see the link side too, not just desc.
    """

    tag_id: int
    title: str | None
    before: str
    after: str | None
    link_url_before: str | None = None
    link_url_after: str | None = None


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

        if kind == "url":
            expanded = _expand_url_span(artist.desc, match)
            if expanded is None:
                report.anomalies.append(
                    f"tag {artist.tag_id} '{artist.title}': text right after the pixiv "
                    "URL doesn't look confidently like more URL -- skipping"
                )
                continue
            span_start, span_end = expanded
        else:
            span_start, span_end = match.start(), match.end()

        try:
            new_desc = _strip_and_tidy(artist.desc, span_start, span_end)
        except _AmbiguousRemnant as exc:
            report.anomalies.append(
                f"tag {artist.tag_id} '{artist.title}': stripping the identity text would "
                f"leave an unclear remnant ({exc}) -- skipping"
            )
            continue

        link_url_before: str | None = None
        link_url_after: str | None = None
        if kind == "url":
            desc_url_text = artist.desc[span_start:span_end]
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
                    link_url_before = own_link.url
                    link_url_after = desc_url_text
                    if apply:
                        own_link.url = desc_url_text
            report.descs_cleaned += 1
        else:
            report.bare_text_stripped += 1

        report.samples.append(
            DescMoveSample(
                tag_id=artist.tag_id,  # type: ignore[arg-type]
                title=artist.title,
                before=artist.desc,
                after=new_desc,
                link_url_before=link_url_before,
                link_url_after=link_url_after,
            )
        )

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

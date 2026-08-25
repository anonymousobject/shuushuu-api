"""Stamp contextual compound-search sources onto built image responses.

For each character tag on an image, if the image carries EXACTLY ONE source
tag linked to that character (character_source_links), that TagSummary gets
context_source_tag_id = the source's canonical id; zero or multiple linked
sources leave it None. Measured on dev 2026-07-28 across 1.32M
image↔linked-character pairs: 97.4% exactly-one / 2.4% zero / 0.2%
ambiguous — ranking is deliberately absent. At most two small indexed
queries per request (alias map + links), skipped when the page has no
character tags.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from app.schemas.image import ImageDetailedResponse


async def stamp_context_sources(
    db: AsyncSession, responses: Sequence[ImageDetailedResponse]
) -> None:
    """Mutate responses in place per the exactly-one rule."""
    page_tag_ids: set[int] = set()
    has_character = False
    for r in responses:
        for t in r.tags or []:
            if t.type_id in (TagType.CHARACTER, TagType.SOURCE):
                page_tag_ids.add(t.tag_id)
                has_character = has_character or t.type_id == TagType.CHARACTER
    # Sources alone can never be stamped, so a character-less page needs no
    # query at all — not even the alias map, which exists to canonicalize the
    # character ids this rule keys on.
    if not has_character:
        return

    # Canonicalize alias tags among them (usually zero rows).
    alias_rows = (
        await db.execute(
            select(Tags.tag_id, Tags.alias_of).where(  # type: ignore[call-overload]
                Tags.tag_id.in_(page_tag_ids),  # type: ignore[union-attr]
                Tags.alias_of.is_not(None),  # type: ignore[union-attr]
            )
        )
    ).all()
    canon: dict[int, int] = dict(alias_rows)  # type: ignore[arg-type]

    # Non-empty by construction: has_character above was set by a tag this
    # same comprehension collects. (It used to be the guard that caught
    # source-only pages, one query too late.)
    char_ids = {
        canon.get(t.tag_id, t.tag_id)
        for r in responses
        for t in r.tags or []
        if t.type_id == TagType.CHARACTER
    }

    link_rows = (
        await db.execute(
            select(  # type: ignore[call-overload]
                CharacterSourceLinks.character_tag_id,
                CharacterSourceLinks.source_tag_id,
            ).where(CharacterSourceLinks.character_tag_id.in_(char_ids))  # type: ignore[attr-defined]
        )
    ).all()
    links: dict[int, set[int]] = {}
    for char_id, source_id in link_rows:
        links.setdefault(char_id, set()).add(source_id)
    if not links:
        return

    for r in responses:
        if not r.tags:
            continue
        image_sources = {
            canon.get(t.tag_id, t.tag_id) for t in r.tags if t.type_id == TagType.SOURCE
        }
        for t in r.tags:
            if t.type_id != TagType.CHARACTER:
                continue
            linked = links.get(canon.get(t.tag_id, t.tag_id))
            if not linked:
                continue
            present = image_sources & linked
            if len(present) == 1:
                t.context_source_tag_id = next(iter(present))

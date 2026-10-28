"""TTL-cached image counts shared between a tag and its character-source links.

A source page ranks its linked characters, and a character page its linked
sources, by how many images carry *both* tags — not by the linked tag's global
``usage_count``, which floats a big-franchise character to the top of a source
it barely appears in (Charlotte: 841 images total, 8 of them Genshin Impact).

The count is a raw ``tag_links`` intersection: no status filter, no repost
filter, no tag hierarchy — the same semantics as the ``usage_count`` it
replaces for ordering, and the reason it can be cached once for every viewer.
The join has to expand every tag on every image of the anchor tag, which costs
~140ms on the largest source in the corpus (Pokémon: 1006 linked characters
over 16.5k images) and a few ms on a typical one, so the result is cached.
"""

import json
from typing import Literal

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.character_source_link import CharacterSourceLinks
from app.models.tag_link import TagLinks

# TTL-only - no per-mutation invalidation. Adding or removing one tag moves one
# count by one, which almost never reorders the list, so a quarter-hour of lag
# is not worth coupling the cache to every tag_links write.
SHARED_COUNT_TTL = 900

LinkedSide = Literal["character", "source"]


def _cache_key(anchor_tag_id: int, linked_side: LinkedSide) -> str:
    return f"csl:shared:{linked_side[0]}:{anchor_tag_id}"


async def get_shared_image_counts(
    db: AsyncSession,
    anchor_tag_id: int,
    linked_side: LinkedSide,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> dict[int, int]:
    """Map linked tag_id -> images carrying both it and ``anchor_tag_id``.

    ``linked_side`` names the side of the link being counted: ``"character"``
    when viewing a source, ``"source"`` when viewing a character. Tags linked
    to the anchor but sharing no image are absent from the map, so callers
    should read it with a ``0`` default.
    """
    key = _cache_key(anchor_tag_id, linked_side)
    if redis_client is not None:
        cached = await redis_client.get(key)
        if cached is not None:
            return {int(tag_id): count for tag_id, count in json.loads(cached).items()}

    if linked_side == "character":
        linked_col = CharacterSourceLinks.character_tag_id
        anchor_col = CharacterSourceLinks.source_tag_id
    else:
        linked_col = CharacterSourceLinks.source_tag_id
        anchor_col = CharacterSourceLinks.character_tag_id

    anchor_links = aliased(TagLinks)
    linked_links = aliased(TagLinks)
    query = (
        select(linked_links.tag_id, func.count())  # type: ignore[call-overload]
        .select_from(anchor_links)
        .join(linked_links, linked_links.image_id == anchor_links.image_id)
        .join(CharacterSourceLinks, linked_col == linked_links.tag_id)
        .where(anchor_links.tag_id == anchor_tag_id, anchor_col == anchor_tag_id)
        .group_by(linked_links.tag_id)
    )
    counts: dict[int, int] = dict((await db.execute(query)).all())  # type: ignore[arg-type]

    if redis_client is not None:
        await redis_client.setex(key, SHARED_COUNT_TTL, json.dumps(counts))
    return counts

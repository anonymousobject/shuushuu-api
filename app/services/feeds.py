"""Atom feed rendering and query helpers."""

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.tag_link import TagLinks

SentinelRow = tuple[int, datetime | None]

_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def mime_type_for_ext(ext: str) -> str:
    """Map a file extension (case-insensitive, no leading dot) to its MIME type.

    Returns 'application/octet-stream' for unknown or empty extensions — Atom
    validators accept this and feed readers handle it gracefully.
    """
    return _MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def _pick_representative(tags: list[Any], type_value: int) -> str | None:
    """Return the title of the highest-usage tag of the given type, or None."""
    candidates = [t for t in tags if t.type_id == type_value]
    if not candidates:
        return None
    # Stable sort: usage_count DESC, then tag_id ASC for determinism on ties.
    candidates.sort(key=lambda t: (-t.usage_count, t.tag_id))
    return candidates[0].tag


def compose_entry_title(image_id: int, tags: list[Any]) -> str:
    """Build the Atom entry <title> per the design spec.

    Format: '{characters} ({sources}) by {artists}' — single representative tag
    per category (highest usage_count). Empty sections are skipped. Falls back
    to 'Image #{image_id}' if no character, source, or artist tags are present.

    `tags` is a sequence of TagSummary-shaped objects with `.tag` (title),
    `.type_id` (int, matching TagType constants), `.tag_id`, and `.usage_count`.
    """
    char = _pick_representative(tags, TagType.CHARACTER)
    src = _pick_representative(tags, TagType.SOURCE)
    artist = _pick_representative(tags, TagType.ARTIST)

    parts: list[str] = []
    if char:
        parts.append(char)
    if src:
        parts.append(f"({src})")
    if artist:
        parts.append(f"by {artist}")

    if not parts:
        return f"Image #{image_id}"
    return " ".join(parts)


def compute_feed_etag(sentinel: list[SentinelRow]) -> str:
    """Derive a weak ETag from the sentinel query result.

    Rows with NULL date_added are excluded from the hash (defensive per spec).
    The hash is stable for identical input, which is all a conditional request
    needs — we regenerate it from current DB state on every request and never
    store it.
    """
    payload = ",".join(
        f"{image_id}:{ts.isoformat()}" for image_id, ts in sentinel if ts is not None
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def newest_timestamp(sentinel: list[SentinelRow]) -> datetime | None:
    """Return the newest non-NULL date_added from the sentinel, floored to the second.

    Returns None if the sentinel is empty or every row has NULL date_added —
    callers should omit the Last-Modified header in that case.
    """
    non_null = [ts for _, ts in sentinel if ts is not None]
    if not non_null:
        return None
    return max(non_null).replace(microsecond=0)


async def fetch_feed_sentinel(
    db: AsyncSession,
    tag_ids: list[int] | None,
    limit: int = 50,
) -> list[SentinelRow]:
    """Return [(image_id, date_added), ...] for the feed window.

    Cheap query — indexed scan on (status, image_id DESC) only, no joins beyond
    an optional IN subquery for per-tag filtering. Used for ETag derivation and
    to short-circuit the full hydration query on conditional-request hits.

    Args:
        tag_ids: None for the global feed; a non-empty list (the already-resolved
            alias + hierarchy-expanded tag IDs) for per-tag. An empty list returns
            no rows.
    """
    if tag_ids == []:
        return []

    query = (
        select(Images.image_id, Images.date_added)
        .where(Images.status == ImageStatus.ACTIVE)
        .order_by(Images.image_id.desc())
        .limit(limit)
    )

    if tag_ids is not None:
        tag_subquery = (
            select(TagLinks.image_id).where(TagLinks.tag_id.in_(tag_ids)).distinct().subquery()
        )
        query = query.where(Images.image_id.in_(select(tag_subquery)))

    result = await db.execute(query)
    return [(row.image_id, row.date_added) for row in result]

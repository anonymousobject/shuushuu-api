"""Atom feed rendering and query helpers."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any

from feedgenerator import Atom1Feed, Enclosure  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import ImageStatus, TagType, settings
from app.models.image import Images
from app.models.tag_link import TagLinks
from app.schemas.image import ImageDetailedResponse

TAG_TYPE_NAME: dict[int, str] = {
    TagType.THEME: "Theme",
    TagType.SOURCE: "Source",
    TagType.ARTIST: "Artist",
    TagType.CHARACTER: "Character",
    # TagType.ALL is a filter pseudo-type; never on actual rows.
}

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
    return candidates[0].tag  # type: ignore[no-any-return]


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
        select(Images.image_id, Images.date_added)  # type: ignore[call-overload]
        .where(Images.status == ImageStatus.ACTIVE)
        .order_by(Images.image_id.desc())  # type: ignore[union-attr]
        .limit(limit)
    )

    if tag_ids is not None:
        query = query.where(
            Images.image_id.in_(  # type: ignore[union-attr]
                select(TagLinks.image_id)  # type: ignore[call-overload]
                .where(TagLinks.tag_id.in_(tag_ids))  # type: ignore[attr-defined]
                .distinct()
            )
        )

    result = await db.execute(query)
    return [(row.image_id, row.date_added) for row in result]


async def fetch_feed_entries(
    db: AsyncSession,
    tag_ids: list[int] | None,
    limit: int = 50,
) -> list[ImageDetailedResponse]:
    """Full hydration query for feed rendering.

    Eager-loads the uploader and every linked tag (plus the tag row itself for
    title/type). Converts results via ImageDetailedResponse.from_db_model, which
    handles the tag_links -> tags mapping that from_attributes cannot do.
    """
    if tag_ids == []:
        return []

    query = (
        select(Images)
        .options(
            selectinload(Images.user),  # type: ignore[arg-type]
            selectinload(Images.tag_links).selectinload(TagLinks.tag),  # type: ignore[arg-type]
        )
        .where(Images.status == ImageStatus.ACTIVE)  # type: ignore[arg-type]
        .order_by(Images.image_id.desc())  # type: ignore[union-attr]
        .limit(limit)
    )

    if tag_ids is not None:
        query = query.where(
            Images.image_id.in_(  # type: ignore[union-attr]
                select(TagLinks.image_id)  # type: ignore[call-overload]
                .where(TagLinks.tag_id.in_(tag_ids))  # type: ignore[attr-defined]
                .distinct()
            )
        )

    result = await db.execute(query)
    images = result.scalars().all()
    return [ImageDetailedResponse.from_db_model(image) for image in images]


class _ShuushuuAtom1Feed(Atom1Feed):  # type: ignore[misc]
    """Atom1Feed variant that emits <category term=... scheme=...> per RFC 4287.

    The stock Atom1Feed.add_item_elements drops the scheme attribute, so we
    append our own category elements from a 'shuu_categories' item attribute
    (list of (term, scheme) tuples) passed through add_item's **kwargs.
    """

    def add_item_elements(self, handler, item):  # type: ignore[no-untyped-def]
        super().add_item_elements(handler, item)
        for term, scheme in item.get("shuu_categories", ()):
            handler.addQuickElement("category", "", {"term": term, "scheme": scheme})


@dataclass(frozen=True)
class FeedMeta:
    feed_id: str
    title: str
    self_url: str
    alternate_url: str


def _category_scheme(tag_type_id: int) -> str:
    return (
        f"{settings.FRONTEND_URL.rstrip('/')}/tag-type/{TAG_TYPE_NAME.get(tag_type_id, 'Unknown')}"
    )


def build_atom_feed(
    meta: FeedMeta,
    entries: list[ImageDetailedResponse],
) -> str:
    """Render an Atom 1.0 XML document."""
    feed = _ShuushuuAtom1Feed(
        title=meta.title,
        link=meta.alternate_url,
        description=None,  # explicit: no <subtitle>
        feed_url=meta.self_url,
        language="en",
    )
    # Override <id> and feed-level <author>. <updated> is auto-derived.
    feed.feed["id"] = meta.feed_id
    feed.feed["author_name"] = "Shuushuu"

    frontend = settings.FRONTEND_URL.rstrip("/")

    for image in entries:
        author_name = (
            image.user.username if image.user and image.user.username else "[deleted user]"
        )
        # date_added is NOT NULL at the DB level; this fallback is for the
        # impossible case only. Use a fixed epoch rather than wall-clock time so
        # a misconfigured row doesn't churn the entry's timestamp on every poll.
        entry_dt = image.date_added or datetime(1970, 1, 1, tzinfo=UTC)

        content_html: str | None = escape(image.caption) if image.caption else None

        shuu_categories = [(t.tag, _category_scheme(t.type_id)) for t in (image.tags or [])]

        enclosure = Enclosure(
            url=image.url,
            length=str(image.filesize or 0),
            mime_type=mime_type_for_ext(image.ext),
        )

        feed.add_item(
            title=compose_entry_title(image_id=image.image_id, tags=image.tags or []),
            link=f"{frontend}/images/{image.image_id}",
            description=None,
            content=content_html,
            unique_id=f"tag:e-shuushuu.net,2005:image:{image.image_id}",
            unique_id_is_permalink=False,
            pubdate=entry_dt,
            updateddate=entry_dt,
            author_name=author_name,
            enclosures=[enclosure],
            shuu_categories=shuu_categories,
        )

    return feed.writeString("utf-8")  # type: ignore[no-any-return]

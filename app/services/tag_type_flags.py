"""Maintain denormalized per-image tag-type presence flags on the images table.

Source of truth is tag_links + tags.type; these helpers recompute the cached
has_theme/has_source/has_artist/has_character columns from it (idempotent).
"""

from collections.abc import Collection

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# Single set-based recompute over a set of image_ids. MariaDB multi-table UPDATE;
# MAX(t.type = N) is a boolean aggregate (1 if any tag of that type, else 0).
_RECOMPUTE_SQL = text(
    """
    UPDATE images i
    LEFT JOIN (
        SELECT tl.image_id,
               MAX(t.type = 1) AS ht,
               MAX(t.type = 2) AS hs,
               MAX(t.type = 3) AS ha,
               MAX(t.type = 4) AS hc
        FROM tag_links tl
        JOIN tags t ON tl.tag_id = t.tag_id
        WHERE tl.image_id IN :ids
        GROUP BY tl.image_id
    ) agg ON agg.image_id = i.image_id
    SET i.has_theme = COALESCE(agg.ht, 0),
        i.has_source = COALESCE(agg.hs, 0),
        i.has_artist = COALESCE(agg.ha, 0),
        i.has_character = COALESCE(agg.hc, 0)
    WHERE i.image_id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


async def refresh_images_tag_type_flags(db: AsyncSession, image_ids: Collection[int]) -> None:
    """Recompute the 4 tag-type presence flags for the given images from tag_links.

    Idempotent. Does NOT commit — joins the caller's transaction. Flushes first
    because the session is autoflush=False and add-tag paths leave pending,
    unflushed TagLinks the recompute SELECT must see.

    Bypasses the ORM identity map — any Images instance already loaded in the
    session keeps stale has_* attributes until refresh/expire. Assert by
    re-querying, not on a pre-fetched object.
    """
    ids = list({int(i) for i in image_ids})
    if not ids:
        return
    await db.flush()
    await db.execute(_RECOMPUTE_SQL, {"ids": ids})


async def refresh_image_tag_type_flags(db: AsyncSession, image_id: int) -> None:
    """Convenience wrapper for a single image."""
    await refresh_images_tag_type_flags(db, [image_id])

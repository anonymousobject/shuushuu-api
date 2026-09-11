"""Maintain denormalized per-image tag-type presence flags on the images table.

Source of truth is tag_links + tags.type; these helpers recompute the cached
has_theme/has_source/has_artist/has_character columns from it (idempotent).
"""

from collections.abc import Collection

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# Single set-based recompute over a set of image_ids: UPDATE ... FROM with
# bool_or. The subquery LEFT JOINs from images so every requested id gets an
# agg row (all-NULL flags when untagged), and COALESCE resets those to false.
_RECOMPUTE_SQL = text(
    """
    UPDATE images
    SET has_theme = COALESCE(agg.ht, FALSE),
        has_source = COALESCE(agg.hs, FALSE),
        has_artist = COALESCE(agg.ha, FALSE),
        has_character = COALESCE(agg.hc, FALSE)
    FROM (
        SELECT i2.image_id,
               bool_or(t.type = 1) AS ht,
               bool_or(t.type = 2) AS hs,
               bool_or(t.type = 3) AS ha,
               bool_or(t.type = 4) AS hc
        FROM images i2
        LEFT JOIN tag_links tl ON tl.image_id = i2.image_id
        LEFT JOIN tags t ON t.tag_id = tl.tag_id
        WHERE i2.image_id IN :ids
        GROUP BY i2.image_id
    ) AS agg
    WHERE images.image_id = agg.image_id
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

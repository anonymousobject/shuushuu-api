"""TTL-cached global image counts for the default-feed pagination total.

``list_images`` computes the bare default-feed total as ``count(visible) + count(my
own hidden)``, where ``count(visible) = count(all) - count(hidden)``. The two global
counts (``count(all)`` and ``count(hidden)``) are the same for every viewer, so we
cache them with a short TTL rather than recomputing per request. The cached total can
lag an image create/delete/status-change by up to the TTL — a non-issue for a
pagination counter over a million-row feed, and not worth the invalidation coupling.
"""

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

# TTL-only — no per-mutation invalidation. The count can lag a create/delete/status
# change by at most FEED_COUNT_TTL seconds; acceptable for a pagination counter.
FEED_COUNT_TTL = 60

_KEY_TOTAL = "feed:count:total"
_KEY_HIDDEN = "feed:count:hidden"


async def get_feed_counts(
    db: AsyncSession,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> tuple[int, int]:
    """Return ``(count_all, count_hidden)``, cache-backed when a Redis client is given.

    On a cache hit returns the stored values; on a miss (or no client) computes both
    from the DB and caches them. ``status NOT IN PUBLIC`` is index-backed (idx_status),
    so the miss path is still cheap relative to the naive OR scan.
    """
    if redis_client is not None:
        cached_total = await redis_client.get(_KEY_TOTAL)
        cached_hidden = await redis_client.get(_KEY_HIDDEN)
        if cached_total is not None and cached_hidden is not None:
            return int(cached_total), int(cached_hidden)

    total = (await db.execute(select(func.count()).select_from(Images))).scalar() or 0
    hidden = (
        await db.execute(
            select(func.count())
            .select_from(Images)
            .where(Images.status.notin_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]
        )
    ).scalar() or 0

    if redis_client is not None:
        await redis_client.setex(_KEY_TOTAL, FEED_COUNT_TTL, total)
        await redis_client.setex(_KEY_HIDDEN, FEED_COUNT_TTL, hidden)

    return total, hidden

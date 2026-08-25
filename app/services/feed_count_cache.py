"""TTL-cached global image counts for the default-feed pagination total.

``list_images`` computes the bare default-feed total as ``count(visible) + count(my
own hidden)``, where ``count(visible) = count(all) - count(hidden)``. The three global
counts (``count(all)``, ``count(hidden)``, and ``count(repost)``) are the same for
every viewer, so we cache them with a short TTL rather than recomputing per request.
The cached total can lag an image create/delete/status-change by up to the TTL — a
non-issue for a pagination counter over a million-row feed, and not worth the
invalidation coupling.
"""

import hashlib
from typing import Any

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.config import ImageStatus
from app.models.image import Images
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

# TTL-only — no per-mutation invalidation. The count can lag a create/delete/status
# change by at most FEED_COUNT_TTL seconds; acceptable for a pagination counter.
FEED_COUNT_TTL = 60

_KEY_TOTAL = "feed:count:total"
_KEY_HIDDEN = "feed:count:hidden"
_KEY_REPOST = "feed:count:repost"
_FILTERED_KEY_PREFIX = "feed:count:filtered:"


async def get_feed_counts(
    db: AsyncSession,
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> tuple[int, int, int]:
    """Return ``(count_all, count_hidden, count_repost)``, cache-backed when a client is given.

    On a cache hit returns the stored values; on a miss (or no client) computes all
    three from the DB and caches them. ``status NOT IN PUBLIC`` is index-backed
    (idx_status), so the miss path is still cheap relative to the naive OR scan.
    The ``status == REPOST`` equality scan hits the same index, so it is equally cheap.
    """
    if redis_client is not None:
        # Three separate .get() calls (not .mget): the test mock_redis stubs .get but not
        # .mget, so .mget would silently miss the cache in tests.
        cached_total = await redis_client.get(_KEY_TOTAL)
        cached_hidden = await redis_client.get(_KEY_HIDDEN)
        cached_repost = await redis_client.get(_KEY_REPOST)
        if cached_total is not None and cached_hidden is not None and cached_repost is not None:
            return int(cached_total), int(cached_hidden), int(cached_repost)

    total = (await db.execute(select(func.count()).select_from(Images))).scalar() or 0
    hidden = (
        await db.execute(
            select(func.count())
            .select_from(Images)
            .where(Images.status.notin_(PUBLIC_IMAGE_STATUSES))  # type: ignore[attr-defined]
        )
    ).scalar() or 0
    repost = (
        await db.execute(
            select(func.count()).select_from(Images).where(Images.status == ImageStatus.REPOST)  # type: ignore[arg-type]
        )
    ).scalar() or 0

    if redis_client is not None:
        await redis_client.setex(_KEY_TOTAL, FEED_COUNT_TTL, total)
        await redis_client.setex(_KEY_HIDDEN, FEED_COUNT_TTL, hidden)
        await redis_client.setex(_KEY_REPOST, FEED_COUNT_TTL, repost)

    return total, hidden, repost


def filtered_count_key(count_query: Select[Any]) -> str:
    """Cache key for a filtered list_images count: hash of compiled SQL + bind params.

    Keying on the compiled query (rather than a hand-assembled filter signature)
    guarantees every WHERE clause — including the viewer-visibility branch and any
    filter added later — is part of the key, so distinct queries can never share
    an entry. The cost is key churn when the generated SQL changes (SQLAlchemy
    upgrade, query refactor): entries miss once and repopulate.
    """
    compiled = count_query.compile()
    material = str(compiled) + "|" + repr(sorted(compiled.params.items()))
    return _FILTERED_KEY_PREFIX + hashlib.sha256(material.encode()).hexdigest()


async def get_filtered_count(
    db: AsyncSession,
    count_query: Select[Any],
    redis_client: redis.Redis | None = None,  # type: ignore[type-arg]
) -> int:
    """TTL-cached pagination total for a filtered (non-bare-feed) list_images query.

    Popular tag filters make the exact count a ~million-row semijoin (~700ms) that
    was recomputed on every page of every viewer; the page query itself is ~1ms.
    Same staleness contract as the global feed counts above.
    """
    key = filtered_count_key(count_query)
    if redis_client is not None:
        cached = await redis_client.get(key)
        if cached is not None:
            return int(cached)

    total = (await db.execute(count_query)).scalar() or 0

    if redis_client is not None:
        await redis_client.setex(key, FEED_COUNT_TTL, total)
    return total

"""Banner service for caching and retrieval.

Handles Redis caching of randomly selected banners with theme and size filtering.
"""

import random
from typing import Any, cast

import redis.asyncio as redis
from fastapi import HTTPException
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.config import settings
from app.models.misc import Banners, BannerSize
from app.schemas.banner import BannerResponse

BANNER_CACHE_KEY_PREFIX = "banner:current:"


def _make_cache_key(theme: str, size: str) -> str:
    return f"{BANNER_CACHE_KEY_PREFIX}{theme}:{size}"


def _compute_ttl_seconds() -> int:
    ttl = settings.BANNER_CACHE_TTL
    if settings.BANNER_CACHE_TTL_JITTER:
        ttl += random.randint(0, settings.BANNER_CACHE_TTL_JITTER)
    return ttl


async def get_current_banner(
    theme: str,
    size: str,
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
) -> BannerResponse:
    """Get the current banner for a theme and size.

    Checks Redis cache first. On cache miss, queries the database for eligible banners,
    filters invalid rows via BannerResponse validation, selects randomly, caches, and returns.
    """

    cache_key = _make_cache_key(theme, size)

    cached = await redis_client.get(cache_key)
    if cached:
        cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        try:
            return BannerResponse.model_validate_json(cached_str)
        except Exception:
            # Stale/invalid cache entry - delete and fall through to DB lookup
            await redis_client.delete(cache_key)

    # Validate inputs minimally (API layer should also validate)
    if theme not in {"dark", "light"}:
        raise HTTPException(status_code=400, detail=f"Invalid theme '{theme}'")

    try:
        size_enum = BannerSize(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid size '{size}'") from exc

    theme_filter = Banners.supports_dark if theme == "dark" else Banners.supports_light

    active_filter = cast(ColumnElement[bool], Banners.active == True)  # noqa: E712
    theme_filter_expr = cast(ColumnElement[bool], theme_filter == True)  # noqa: E712
    size_filter = cast(ColumnElement[bool], Banners.size == size_enum)

    query = select(Banners).where(active_filter, theme_filter_expr, size_filter)

    result = await db.execute(query)
    banners = result.scalars().all()

    if not banners:
        raise HTTPException(
            status_code=404,
            detail=f"No banners available for theme '{theme}' and size '{size}'",
        )

    valid_responses: list[BannerResponse] = []
    for banner in banners:
        try:
            valid_responses.append(BannerResponse.model_validate(banner))
        except Exception:
            continue

    if not valid_responses:
        raise HTTPException(
            status_code=404,
            detail=f"No valid banners available for theme '{theme}' and size '{size}'",
        )

    response = random.choice(valid_responses)

    await redis_client.setex(cache_key, _compute_ttl_seconds(), response.model_dump_json())

    return response


async def list_banners(
    db: AsyncSession,
    theme: str | None = None,
    size: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[BannerResponse], int]:
    """List active banners with optional theme/size filtering.

    Invalid layout rows are ignored.
    """

    active_filter = cast(ColumnElement[bool], Banners.active == True)  # noqa: E712
    valid_layout_filter = or_(
        and_(
            cast(Any, Banners.full_image).is_not(None),
            cast(Any, Banners.left_image).is_(None),
            cast(Any, Banners.middle_image).is_(None),
            cast(Any, Banners.right_image).is_(None),
        ),
        and_(
            cast(Any, Banners.full_image).is_(None),
            cast(Any, Banners.left_image).is_not(None),
            cast(Any, Banners.middle_image).is_not(None),
            cast(Any, Banners.right_image).is_not(None),
        ),
    )

    query = select(Banners).where(active_filter, valid_layout_filter)

    if theme is not None:
        if theme == "dark":
            query = query.where(cast(ColumnElement[bool], Banners.supports_dark == True))  # noqa: E712
        elif theme == "light":
            query = query.where(cast(ColumnElement[bool], Banners.supports_light == True))  # noqa: E712
        else:
            raise HTTPException(status_code=400, detail=f"Invalid theme '{theme}'")

    if size is not None:
        try:
            size_enum = BannerSize(size)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid size '{size}'") from exc
        query = query.where(cast(ColumnElement[bool], Banners.size == size_enum))

    # Count total (after filters, before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(desc(cast(Any, Banners.banner_id)))

    offset = max(0, (page - 1) * per_page)
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    banners = result.scalars().all()

    responses = [BannerResponse.model_validate(banner) for banner in banners]
    return responses, total

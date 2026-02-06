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
from app.models.misc import Banners, BannerSize, BannerTheme, UserBannerPins, UserBannerPreferences
from app.schemas.banner import BannerPinResponse, BannerPreferencesResponse, BannerResponse

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
    user_id: int | None = None,
) -> BannerResponse:
    """Get the current banner for a theme and size.

    Checks Redis cache first. On cache miss, queries the database for eligible banners,
    filters invalid rows via BannerResponse validation, selects randomly, caches, and returns.

    If user_id is provided, resolves preferred size and pinned banners before rotation.
    """

    # Validate inputs early (API layer should also validate)
    if theme not in {"dark", "light"}:
        raise HTTPException(status_code=400, detail=f"Invalid theme '{theme}'")

    try:
        size_enum = BannerSize(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid size '{size}'") from exc

    effective_size = size_enum

    if user_id is not None:
        # Check for user preferences
        prefs = await db.get(UserBannerPreferences, user_id)
        if prefs:
            effective_size = prefs.preferred_size

        # Check for pinned banner
        theme_enum = BannerTheme(theme)
        pin_query = select(UserBannerPins).where(
            cast(ColumnElement[bool], UserBannerPins.user_id == user_id),
            cast(ColumnElement[bool], UserBannerPins.size == effective_size),
            cast(ColumnElement[bool], UserBannerPins.theme == theme_enum),
        )
        pin_result = await db.execute(pin_query)
        pin = pin_result.scalar_one_or_none()

        if pin:
            banner = await db.get(Banners, pin.banner_id)
            if banner and banner.active:
                try:
                    return BannerResponse.model_validate(banner)
                except Exception:
                    pass  # Invalid layout, fall through to rotation

    cache_key = _make_cache_key(theme, effective_size.value)

    cached = await redis_client.get(cache_key)
    if cached:
        cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        try:
            return BannerResponse.model_validate_json(cached_str)
        except Exception:
            # Stale/invalid cache entry - delete and fall through to DB lookup
            await redis_client.delete(cache_key)

    theme_filter = Banners.supports_dark if theme == "dark" else Banners.supports_light

    active_filter = cast(ColumnElement[bool], Banners.active == True)  # noqa: E712
    theme_filter_expr = cast(ColumnElement[bool], theme_filter == True)  # noqa: E712
    size_filter = cast(ColumnElement[bool], Banners.size == effective_size)

    query = select(Banners).where(active_filter, theme_filter_expr, size_filter)

    result = await db.execute(query)
    banners = result.scalars().all()

    if not banners:
        raise HTTPException(
            status_code=404,
            detail=f"No banners available for theme '{theme}' and size '{effective_size.value}'",
        )

    valid_responses: list[BannerResponse] = []
    for banner in banners:
        try:
            valid_responses.append(BannerResponse.model_validate(banner))
        except Exception:
            continue  # Skip banners with invalid layout (e.g. partial three-part)

    if not valid_responses:
        raise HTTPException(
            status_code=404,
            detail=f"No valid banners available for theme '{theme}' and size '{effective_size.value}'",
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


async def get_user_preferences(
    user_id: int,
    db: AsyncSession,
) -> BannerPreferencesResponse:
    """Get a user's banner preferences, returning defaults if no row exists."""

    prefs = await db.get(UserBannerPreferences, user_id)
    preferred_size = prefs.preferred_size if prefs else BannerSize.small

    # Fetch all pins for this user with their banners
    pin_query = select(UserBannerPins).where(
        cast(ColumnElement[bool], UserBannerPins.user_id == user_id)
    )
    pin_result = await db.execute(pin_query)
    pin_rows = pin_result.scalars().all()

    pins: list[BannerPinResponse] = []
    for pin_row in pin_rows:
        banner = await db.get(Banners, pin_row.banner_id)
        banner_response = None
        if banner:
            try:
                banner_response = BannerResponse.model_validate(banner)
            except Exception:
                pass  # Skip banners with invalid layout (e.g. partial three-part)
        pins.append(
            BannerPinResponse(
                size=pin_row.size,
                theme=pin_row.theme,
                banner=banner_response,
            )
        )

    return BannerPreferencesResponse(preferred_size=preferred_size, pins=pins)


async def update_preferred_size(
    user_id: int,
    size: BannerSize,
    db: AsyncSession,
) -> None:
    """Update (or create) a user's preferred banner size."""

    prefs = await db.get(UserBannerPreferences, user_id)
    if prefs:
        prefs.preferred_size = size
    else:
        prefs = UserBannerPreferences(user_id=user_id, preferred_size=size)
        db.add(prefs)
    await db.commit()


async def pin_banner(
    user_id: int,
    size: BannerSize,
    theme: BannerTheme,
    banner_id: int,
    db: AsyncSession,
) -> None:
    """Pin a banner for a user's size+theme slot.

    Validates that the banner exists, is active, and matches the requested size and theme.
    Upserts if a pin already exists for this slot.
    """

    banner = await db.get(Banners, banner_id)
    if not banner:
        raise HTTPException(status_code=404, detail="Banner not found")
    if not banner.active:
        raise HTTPException(status_code=400, detail="Cannot pin an inactive banner")
    if banner.size != size:
        raise HTTPException(
            status_code=400,
            detail=f"Banner size '{banner.size.value}' does not match requested size '{size.value}'",
        )
    theme_supported = banner.supports_dark if theme == BannerTheme.dark else banner.supports_light
    if not theme_supported:
        raise HTTPException(
            status_code=400,
            detail=f"Banner does not support theme '{theme.value}'",
        )

    # Upsert: find existing pin for this slot or create new
    existing_query = select(UserBannerPins).where(
        cast(ColumnElement[bool], UserBannerPins.user_id == user_id),
        cast(ColumnElement[bool], UserBannerPins.size == size),
        cast(ColumnElement[bool], UserBannerPins.theme == theme),
    )
    result = await db.execute(existing_query)
    existing = result.scalar_one_or_none()

    if existing:
        existing.banner_id = banner_id
    else:
        pin = UserBannerPins(
            user_id=user_id,
            size=size,
            theme=theme,
            banner_id=banner_id,
        )
        db.add(pin)
    await db.commit()


async def unpin_banner(
    user_id: int,
    size: BannerSize,
    theme: BannerTheme,
    db: AsyncSession,
) -> None:
    """Remove a pin for a user's size+theme slot. Raises 404 if no pin exists."""

    query = select(UserBannerPins).where(
        cast(ColumnElement[bool], UserBannerPins.user_id == user_id),
        cast(ColumnElement[bool], UserBannerPins.size == size),
        cast(ColumnElement[bool], UserBannerPins.theme == theme),
    )
    result = await db.execute(query)
    pin = result.scalar_one_or_none()

    if not pin:
        raise HTTPException(status_code=404, detail="No pin found for this slot")

    await db.delete(pin)
    await db.commit()

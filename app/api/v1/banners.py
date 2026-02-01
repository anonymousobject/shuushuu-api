"""Banner API endpoints."""

from typing import Annotated, Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.misc import BannerSize
from app.schemas.banner import BannerListResponse, BannerResponse
from app.services.banner import get_current_banner, list_banners

router = APIRouter(prefix="/banners", tags=["banners"])


@router.get("/current", response_model=BannerResponse)
async def current_banner(
    theme: Annotated[Literal["dark", "light"], Query(description="Theme for banner selection")],
    size: Annotated[BannerSize, Query(description="Banner size")],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> BannerResponse:
    return await get_current_banner(theme, size.value, db, redis_client)


@router.get("", response_model=BannerListResponse)
async def list_active_banners(
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()] = PaginationParams(),
    theme: Annotated[Literal["dark", "light"] | None, Query()] = None,
    size: Annotated[BannerSize | None, Query()] = None,
) -> BannerListResponse:
    size_value = size.value if size is not None else None
    items, total = await list_banners(
        db,
        theme=theme,
        size=size_value,
        page=pagination.page,
        per_page=pagination.per_page,
    )

    return BannerListResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )

"""Banner API endpoints."""

from typing import Annotated, Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import CurrentUser, OptionalCurrentUser
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.misc import BannerSize, BannerTheme
from app.schemas.banner import (
    BannerListResponse,
    BannerPreferencesResponse,
    BannerResponse,
    PinRequest,
    PreferenceUpdateRequest,
)
from app.services.banner import (
    get_current_banner,
    get_user_preferences,
    list_banners,
    pin_banner,
    unpin_banner,
    update_preferred_size,
)

router = APIRouter(prefix="/banners", tags=["banners"])


@router.get("/current", response_model=BannerResponse)
async def current_banner(
    theme: Annotated[Literal["dark", "light"], Query(description="Theme for banner selection")],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: OptionalCurrentUser = None,
    size: Annotated[BannerSize, Query(description="Banner size")] = BannerSize.small,
) -> BannerResponse:
    user_id = current_user.id if current_user else None
    return await get_current_banner(theme, size.value, db, redis_client, user_id=user_id)


@router.get("/preferences", response_model=BannerPreferencesResponse)
async def get_preferences(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BannerPreferencesResponse:
    return await get_user_preferences(current_user.id, db)


@router.patch("/preferences", response_model=BannerPreferencesResponse)
async def update_preferences(
    body: PreferenceUpdateRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BannerPreferencesResponse:
    await update_preferred_size(current_user.id, body.preferred_size, db)
    return await get_user_preferences(current_user.id, db)


@router.put("/preferences/pins/{size}/{theme}", status_code=status.HTTP_200_OK)
async def pin_banner_endpoint(
    size: BannerSize,
    theme: BannerTheme,
    body: PinRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BannerPreferencesResponse:
    await pin_banner(current_user.id, size, theme, body.banner_id, db)
    return await get_user_preferences(current_user.id, db)


@router.delete(
    "/preferences/pins/{size}/{theme}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unpin_banner_endpoint(
    size: BannerSize,
    theme: BannerTheme,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await unpin_banner(current_user.id, size, theme, db)


@router.get("/", response_model=BannerListResponse)
@router.get("", response_model=BannerListResponse, include_in_schema=False)
async def list_active_banners(
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
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

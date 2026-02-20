"""News API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.auth import CurrentUser
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models.news import News
from app.models.user import Users
from app.schemas.news import NewsCreate, NewsListResponse, NewsResponse, NewsUpdate

router = APIRouter(prefix="/news", tags=["news"])


def _news_with_username_query():  # type: ignore[no-untyped-def]
    """Base query selecting news fields plus username from users join."""
    return select(
        News,
        Users.username,  # type: ignore[call-overload]
    ).join(Users, News.user_id == Users.user_id)


def _to_response(news: News, username: str) -> NewsResponse:
    """Convert a News model + username into a NewsResponse."""
    return NewsResponse(
        news_id=news.news_id,
        user_id=news.user_id,
        username=username,
        title=news.title,
        news_text=news.news_text,
        date=news.date,
        edited=news.edited,
    )


@router.get("/", response_model=NewsListResponse, include_in_schema=False)
@router.get("", response_model=NewsListResponse)
async def list_news(
    pagination: Annotated[PaginationParams, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NewsListResponse:
    """List news items, newest first."""
    # Count total
    count_query = select(func.count()).select_from(News)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Fetch page
    query = (
        _news_with_username_query()  # type: ignore[no-untyped-call]
        .order_by(desc(News.news_id))  # type: ignore[arg-type]
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    result = await db.execute(query)
    rows = result.all()

    return NewsListResponse(
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        news=[_to_response(news, username) for news, username in rows],
    )


@router.get("/{news_id}", response_model=NewsResponse)
async def get_news(
    news_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NewsResponse:
    """Get a single news item by ID."""
    query = _news_with_username_query().where(  # type: ignore[no-untyped-call]
        News.news_id == news_id
    )
    result = await db.execute(query)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="News item not found")

    news, username = row
    return _to_response(news, username)


@router.post(
    "/",
    response_model=NewsResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post("", response_model=NewsResponse, status_code=status.HTTP_201_CREATED)
async def create_news(
    body: NewsCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> NewsResponse:
    """Create a news item. Requires NEWS_CREATE permission."""
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    if not await has_permission(db, current_user.user_id, Permission.NEWS_CREATE, redis_client):
        raise HTTPException(status_code=403, detail="NEWS_CREATE permission required")

    news = News(
        user_id=current_user.user_id,
        title=body.title,
        news_text=body.news_text,
    )
    db.add(news)
    await db.commit()
    await db.refresh(news)

    return _to_response(news, current_user.username)


@router.put("/{news_id}", response_model=NewsResponse)
async def update_news(
    news_id: int,
    body: NewsUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> NewsResponse:
    """Update a news item. Requires NEWS_EDIT permission."""
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    if not await has_permission(db, current_user.user_id, Permission.NEWS_EDIT, redis_client):
        raise HTTPException(status_code=403, detail="NEWS_EDIT permission required")

    result = await db.execute(
        select(News).where(News.news_id == news_id)  # type: ignore[arg-type]
    )
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(status_code=404, detail="News item not found")

    if body.title is not None:
        news.title = body.title
    if body.news_text is not None:
        news.news_text = body.news_text
    news.edited = datetime.now(UTC)

    await db.commit()

    # Re-fetch with username
    query = _news_with_username_query().where(  # type: ignore[no-untyped-call]
        News.news_id == news_id
    )
    result = await db.execute(query)
    row = result.one()
    news, username = row
    return _to_response(news, username)


@router.delete("/{news_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_news(
    news_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    """Delete a news item. Requires NEWS_DELETE permission."""
    # Type narrowing for mypy - user_id is always set for authenticated users
    assert current_user.user_id is not None

    if not await has_permission(db, current_user.user_id, Permission.NEWS_DELETE, redis_client):
        raise HTTPException(status_code=403, detail="NEWS_DELETE permission required")

    result = await db.execute(
        select(News).where(News.news_id == news_id)  # type: ignore[arg-type]
    )
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(status_code=404, detail="News item not found")

    await db.delete(news)
    await db.commit()

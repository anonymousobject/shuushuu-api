# News Items API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CRUD API endpoints for news items with public reads and permission-gated writes.

**Architecture:** Follows existing patterns exactly: SQLModel inheritance (NewsBase already exists), Pydantic schemas for API, explicit SQL joins for username (no relationship on model), permission enum + sync_permissions() for auth. No Alembic migration needed -- `sync_permissions()` auto-seeds new enum entries on startup.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, Pydantic v2, pytest + httpx

**Design doc:** `docs/plans/2026-02-20-news-items-design.md`

---

### Task 1: Add permissions to enum

**Files:**
- Modify: `app/core/permissions.py`

**Step 1: Add NEWS_CREATE, NEWS_EDIT, NEWS_DELETE to Permission enum**

In `app/core/permissions.py`, add after the `POST_EDIT` line (line 63) in the enum:

```python
    # News management
    NEWS_CREATE = "news_create"
    NEWS_EDIT = "news_edit"
    NEWS_DELETE = "news_delete"
```

And add to `_PERMISSION_DESCRIPTIONS` after the `POST_EDIT` entry (line 106):

```python
    # News management
    Permission.NEWS_CREATE: "Create news posts",
    Permission.NEWS_EDIT: "Edit news posts",
    Permission.NEWS_DELETE: "Delete news posts",
```

**Step 2: Run existing permission sync test to confirm no regressions**

Run: `uv run pytest tests/unit/test_permission_sync.py -v`
Expected: All 4 tests PASS (the sync test auto-discovers new enum entries)

**Step 3: Commit**

```bash
git add app/core/permissions.py
git commit -m "feat: add NEWS_CREATE, NEWS_EDIT, NEWS_DELETE permissions"
```

---

### Task 2: Create news schemas

**Files:**
- Create: `app/schemas/news.py`
- Test: `tests/unit/test_news_schemas.py`

**Step 1: Write schema validation tests**

Create `tests/unit/test_news_schemas.py`:

```python
"""Tests for news API schemas."""

import pytest
from pydantic import ValidationError


class TestNewsCreate:
    """Validate NewsCreate schema."""

    def test_valid_create(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="Test News", news_text="Some content")
        assert data.title == "Test News"
        assert data.news_text == "Some content"

    def test_title_required(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(news_text="content")  # type: ignore[call-arg]

    def test_news_text_required(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="Title")  # type: ignore[call-arg]

    def test_title_max_length(self):
        from app.schemas.news import NewsCreate

        with pytest.raises(ValidationError):
            NewsCreate(title="x" * 129, news_text="content")

    def test_title_strips_whitespace(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="  padded  ", news_text="content")
        assert data.title == "padded"

    def test_news_text_strips_whitespace(self):
        from app.schemas.news import NewsCreate

        data = NewsCreate(title="Title", news_text="  padded  ")
        assert data.news_text == "padded"


class TestNewsUpdate:
    """Validate NewsUpdate schema."""

    def test_update_title_only(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(title="New Title")
        assert data.title == "New Title"
        assert data.news_text is None

    def test_update_text_only(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(news_text="New text")
        assert data.news_text == "New text"
        assert data.title is None

    def test_update_both(self):
        from app.schemas.news import NewsUpdate

        data = NewsUpdate(title="Title", news_text="Text")
        assert data.title == "Title"
        assert data.news_text == "Text"

    def test_update_empty_rejected(self):
        from app.schemas.news import NewsUpdate

        with pytest.raises(ValidationError):
            NewsUpdate()

    def test_update_title_max_length(self):
        from app.schemas.news import NewsUpdate

        with pytest.raises(ValidationError):
            NewsUpdate(title="x" * 129)


class TestNewsResponse:
    """Validate NewsResponse schema."""

    def test_response_from_dict(self):
        from datetime import datetime

        from app.schemas.news import NewsResponse

        data = NewsResponse(
            news_id=1,
            user_id=1,
            username="testuser",
            title="Test",
            news_text="Content",
            date=datetime(2026, 1, 1),
            edited=None,
        )
        assert data.news_id == 1
        assert data.username == "testuser"
```

**Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/unit/test_news_schemas.py -v`
Expected: FAIL (ImportError -- schemas don't exist yet)

**Step 3: Create `app/schemas/news.py`**

```python
"""Pydantic schemas for News endpoints."""

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.news import NewsBase
from app.schemas.base import UTCDatetime, UTCDatetimeOptional


class NewsCreate(BaseModel):
    """Schema for creating a news item."""

    title: str = Field(max_length=128, description="News title")
    news_text: str = Field(min_length=1, description="News content (plain text)")

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        return v.strip()

    @field_validator("news_text")
    @classmethod
    def strip_news_text(cls, v: str) -> str:
        return v.strip()


class NewsUpdate(BaseModel):
    """Schema for updating a news item. At least one field must be provided."""

    title: str | None = Field(default=None, max_length=128, description="News title")
    news_text: str | None = Field(default=None, description="News content (plain text)")

    @model_validator(mode="after")
    def at_least_one_field(self) -> "NewsUpdate":
        if self.title is None and self.news_text is None:
            raise ValueError("At least one of title or news_text must be provided")
        return self

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip()

    @field_validator("news_text")
    @classmethod
    def strip_news_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v.strip()


class NewsResponse(NewsBase):
    """Schema for news response -- what the API returns."""

    news_id: int
    user_id: int
    username: str  # From users table join
    date: UTCDatetime
    edited: UTCDatetimeOptional = None

    model_config = {"from_attributes": True}


class NewsListResponse(BaseModel):
    """Schema for paginated news list."""

    total: int
    page: int
    per_page: int
    news: list[NewsResponse]
```

**Step 4: Run tests to confirm they pass**

Run: `uv run pytest tests/unit/test_news_schemas.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/schemas/news.py tests/unit/test_news_schemas.py
git commit -m "feat: add news API schemas with validation"
```

---

### Task 3: Create news route handlers and register router

**Files:**
- Create: `app/api/v1/news.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/api/v1/test_news.py`

**Step 1: Write API tests for all endpoints**

Create `tests/api/v1/test_news.py`:

```python
"""Tests for news API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.news import News
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


@pytest.fixture
async def news_item(db_session: AsyncSession) -> News:
    """Create a test news item owned by user_id=1."""
    item = News(
        user_id=1,
        title="Test News",
        news_text="Test news content",
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


@pytest.fixture
async def user_with_news_create(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_CREATE permission and return (user, token)."""
    user = await db_session.get(Users, 2)
    user.active = 1
    perm = Perms(perm_id=100, title=Permission.NEWS_CREATE.value)
    db_session.add(perm)
    user_perm = UserPerms(user_id=user.user_id, perm_id=100, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()
    token = create_access_token(user.user_id)
    return user, token


@pytest.fixture
async def user_with_news_edit(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_EDIT permission and return (user, token)."""
    user = await db_session.get(Users, 2)
    user.active = 1
    perm = Perms(perm_id=101, title=Permission.NEWS_EDIT.value)
    db_session.add(perm)
    user_perm = UserPerms(user_id=user.user_id, perm_id=101, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()
    token = create_access_token(user.user_id)
    return user, token


@pytest.fixture
async def user_with_news_delete(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_DELETE permission and return (user, token)."""
    user = await db_session.get(Users, 2)
    user.active = 1
    perm = Perms(perm_id=102, title=Permission.NEWS_DELETE.value)
    db_session.add(perm)
    user_perm = UserPerms(user_id=user.user_id, perm_id=102, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()
    token = create_access_token(user.user_id)
    return user, token


@pytest.fixture
async def unprivileged_token(db_session: AsyncSession) -> str:
    """Token for an authenticated user with no news permissions."""
    user = await db_session.get(Users, 3)
    user.active = 1
    await db_session.commit()
    return create_access_token(user.user_id)


class TestListNews:
    """GET /api/v1/news"""

    async def test_list_empty(self, client: AsyncClient):
        """List returns empty result when no news exists."""
        response = await client.get("/api/v1/news")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["news"] == []

    async def test_list_returns_news_with_username(
        self, client: AsyncClient, news_item: News
    ):
        """List returns news items with username from user join."""
        response = await client.get("/api/v1/news")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["news"][0]["news_id"] == news_item.news_id
        assert data["news"][0]["username"] == "testuser"  # user_id=1
        assert data["news"][0]["title"] == "Test News"

    async def test_list_pagination(self, client: AsyncClient, db_session: AsyncSession):
        """List respects pagination parameters."""
        for i in range(3):
            db_session.add(News(user_id=1, title=f"News {i}", news_text=f"Content {i}"))
        await db_session.commit()

        response = await client.get("/api/v1/news?page=1&per_page=2")
        data = response.json()
        assert data["total"] == 3
        assert len(data["news"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2

    async def test_list_ordered_newest_first(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """List returns news ordered by news_id DESC (newest first)."""
        for i in range(3):
            db_session.add(News(user_id=1, title=f"News {i}", news_text=f"Content {i}"))
        await db_session.commit()

        response = await client.get("/api/v1/news")
        data = response.json()
        ids = [item["news_id"] for item in data["news"]]
        assert ids == sorted(ids, reverse=True)


class TestGetNews:
    """GET /api/v1/news/{news_id}"""

    async def test_get_existing(self, client: AsyncClient, news_item: News):
        """Get returns a single news item with username."""
        response = await client.get(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["news_id"] == news_item.news_id
        assert data["username"] == "testuser"

    async def test_get_not_found(self, client: AsyncClient):
        """Get returns 404 for non-existent news_id."""
        response = await client.get("/api/v1/news/99999")
        assert response.status_code == 404


class TestCreateNews:
    """POST /api/v1/news"""

    async def test_create_requires_auth(self, client: AsyncClient):
        """Create returns 401 without authentication."""
        response = await client.post(
            "/api/v1/news", json={"title": "Test", "news_text": "Content"}
        )
        assert response.status_code == 401

    async def test_create_requires_permission(
        self, client: AsyncClient, unprivileged_token: str
    ):
        """Create returns 403 without NEWS_CREATE permission."""
        response = await client.post(
            "/api/v1/news",
            json={"title": "Test", "news_text": "Content"},
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_create_success(
        self, client: AsyncClient, user_with_news_create: tuple[Users, str]
    ):
        """Create returns 201 with valid data and permission."""
        user, token = user_with_news_create
        response = await client.post(
            "/api/v1/news",
            json={"title": "New Post", "news_text": "Post content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Post"
        assert data["news_text"] == "Post content"
        assert data["user_id"] == user.user_id
        assert data["username"] == "testuser2"
        assert data["edited"] is None

    async def test_create_validates_title_required(
        self, client: AsyncClient, user_with_news_create: tuple[Users, str]
    ):
        """Create returns 422 when title is missing."""
        _, token = user_with_news_create
        response = await client.post(
            "/api/v1/news",
            json={"news_text": "Content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422


class TestUpdateNews:
    """PUT /api/v1/news/{news_id}"""

    async def test_update_requires_auth(self, client: AsyncClient, news_item: News):
        """Update returns 401 without authentication."""
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}", json={"title": "Updated"}
        )
        assert response.status_code == 401

    async def test_update_requires_permission(
        self, client: AsyncClient, news_item: News, unprivileged_token: str
    ):
        """Update returns 403 without NEWS_EDIT permission."""
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"title": "Updated"},
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_update_success(
        self, client: AsyncClient, news_item: News, user_with_news_edit: tuple[Users, str]
    ):
        """Update returns 200 and sets edited timestamp."""
        _, token = user_with_news_edit
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"title": "Updated Title"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["news_text"] == "Test news content"  # unchanged
        assert data["edited"] is not None

    async def test_update_not_found(
        self, client: AsyncClient, user_with_news_edit: tuple[Users, str]
    ):
        """Update returns 404 for non-existent news_id."""
        _, token = user_with_news_edit
        response = await client.put(
            "/api/v1/news/99999",
            json={"title": "Updated"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


class TestDeleteNews:
    """DELETE /api/v1/news/{news_id}"""

    async def test_delete_requires_auth(self, client: AsyncClient, news_item: News):
        """Delete returns 401 without authentication."""
        response = await client.delete(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 401

    async def test_delete_requires_permission(
        self, client: AsyncClient, news_item: News, unprivileged_token: str
    ):
        """Delete returns 403 without NEWS_DELETE permission."""
        response = await client.delete(
            f"/api/v1/news/{news_item.news_id}",
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_delete_success(
        self,
        client: AsyncClient,
        news_item: News,
        user_with_news_delete: tuple[Users, str],
    ):
        """Delete returns 204 and removes the news item."""
        _, token = user_with_news_delete
        response = await client.delete(
            f"/api/v1/news/{news_item.news_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        # Confirm it's gone
        response = await client.get(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 404

    async def test_delete_not_found(
        self, client: AsyncClient, user_with_news_delete: tuple[Users, str]
    ):
        """Delete returns 404 for non-existent news_id."""
        _, token = user_with_news_delete
        response = await client.delete(
            "/api/v1/news/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
```

**Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/api/v1/test_news.py -v`
Expected: FAIL (ImportError or 404 -- route doesn't exist yet)

**Step 3: Create `app/api/v1/news.py`**

```python
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


def _news_with_username_query():
    """Base query selecting news fields plus username from users join."""
    return select(
        News,
        Users.username,
    ).join(Users, News.user_id == Users.user_id)


def _to_response(news: News, username: str) -> NewsResponse:
    """Convert a News model + username into a NewsResponse."""
    return NewsResponse(
        news_id=news.news_id,  # type: ignore[arg-type]
        user_id=news.user_id,
        username=username,
        title=news.title,
        news_text=news.news_text,
        date=news.date,  # type: ignore[arg-type]
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
        _news_with_username_query()
        .order_by(desc(News.news_id))
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
    query = _news_with_username_query().where(News.news_id == news_id)
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
    if not await has_permission(
        db, current_user.user_id, Permission.NEWS_CREATE, redis_client  # type: ignore[arg-type]
    ):
        raise HTTPException(status_code=403, detail="NEWS_CREATE permission required")

    news = News(
        user_id=current_user.user_id,  # type: ignore[assignment]
        title=body.title,
        news_text=body.news_text,
    )
    db.add(news)
    await db.commit()
    await db.refresh(news)

    return _to_response(news, current_user.username)  # type: ignore[arg-type]


@router.put("/{news_id}", response_model=NewsResponse)
async def update_news(
    news_id: int,
    body: NewsUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> NewsResponse:
    """Update a news item. Requires NEWS_EDIT permission."""
    if not await has_permission(
        db, current_user.user_id, Permission.NEWS_EDIT, redis_client  # type: ignore[arg-type]
    ):
        raise HTTPException(status_code=403, detail="NEWS_EDIT permission required")

    result = await db.execute(select(News).where(News.news_id == news_id))
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
    query = _news_with_username_query().where(News.news_id == news_id)
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
    if not await has_permission(
        db, current_user.user_id, Permission.NEWS_DELETE, redis_client  # type: ignore[arg-type]
    ):
        raise HTTPException(status_code=403, detail="NEWS_DELETE permission required")

    result = await db.execute(select(News).where(News.news_id == news_id))
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(status_code=404, detail="News item not found")

    await db.delete(news)
    await db.commit()
```

**Step 4: Register the router in `app/api/v1/__init__.py`**

Add import:
```python
from app.api.v1 import (
    ...
    news,
    ...
)
```

Add router registration:
```python
router.include_router(news.router)
```

**Step 5: Run tests to confirm they pass**

Run: `uv run pytest tests/api/v1/test_news.py -v`
Expected: All PASS

**Step 6: Run full test suite to check for regressions**

Run: `uv run pytest -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add app/api/v1/news.py app/api/v1/__init__.py tests/api/v1/test_news.py
git commit -m "feat: add news CRUD API endpoints"
```

---

### Task 4: Final verification

**Step 1: Run mypy type checking**

Run: `uv run mypy app/api/v1/news.py app/schemas/news.py`
Expected: No errors (or only pre-existing type ignores)

**Step 2: Run full test suite one more time**

Run: `uv run pytest -v`
Expected: All PASS

**Step 3: Verify endpoints work against running server (if docker is up)**

```bash
curl -s http://localhost:8000/api/v1/news | python -m json.tool
```

Expected: `{"total": N, "page": 1, "per_page": 20, "news": [...]}`

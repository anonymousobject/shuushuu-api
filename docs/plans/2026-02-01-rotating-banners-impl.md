# Rotating Banners Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a rotating banner system with theme compatibility and size variants.

**Architecture:** Redis-cached random banner selection with 10-minute TTL. Six cache keys (2 themes × 3 sizes). Database stores banner metadata with image paths served directly by nginx.

**Tech Stack:** FastAPI, SQLModel, Redis, Alembic, pytest

---

## Task 1: Add Configuration Settings

**Files:**
- Modify: `app/config.py`

**Step 1: Write the test**

Create `tests/unit/test_banner_config.py` to verify settings exist and defaults are sane.

```python
"""Tests for banner-related config settings."""

from app.config import settings


def test_banner_settings_defaults():
    assert settings.BANNER_BASE_URL
    assert settings.BANNER_CACHE_TTL >= 60
    assert settings.BANNER_CACHE_TTL_JITTER >= 0
```

**Step 2: Add banner configuration settings**

In `app/config.py`, find the `# Avatar Settings` section (around line 88) and add after it:

```python
    # Banner Settings
    BANNER_BASE_URL: str = Field(default="/static/banners")
    BANNER_CACHE_TTL: int = Field(default=600)  # 10 minutes
    BANNER_CACHE_TTL_JITTER: int = Field(default=300)  # up to +5 minutes (optional)
```

**Step 3: Run the test**

```bash
uv run pytest tests/unit/test_banner_config.py -v
```

**Step 4: Commit**

```bash
git add app/config.py tests/unit/test_banner_config.py
git commit -m "feat(banners): add banner configuration settings"
```

---

## Task 2: Update Banner Model

**Files:**
- Modify: `app/models/misc.py`
- Modify: `tests/conftest.py` (import new model)

**Step 1: Write failing test for BannerSize enum**

Create `tests/unit/test_banner_model.py`:

```python
"""Tests for Banner model."""

import pytest
from app.models.misc import BannerSize, Banners


class TestBannerSize:
    """Tests for BannerSize enum."""

    def test_banner_size_values(self):
        """Test BannerSize enum has expected values."""
        assert BannerSize.small == "small"
        assert BannerSize.medium == "medium"
        assert BannerSize.large == "large"

    def test_banner_size_is_string_enum(self):
        """Test BannerSize values are strings."""
        assert isinstance(BannerSize.small.value, str)


class TestBannerModel:
    """Tests for Banners model."""

    def test_banner_has_required_fields(self):
        """Test Banner model has all required fields."""
        banner = Banners(
            name="test_banner",
            full_image="test.png",
        )
        assert banner.name == "test_banner"
        assert banner.size == BannerSize.medium  # default
        assert banner.supports_dark is True  # default
        assert banner.supports_light is True  # default
        assert banner.active is True  # default

    def test_banner_three_part_fields(self):
        """Test Banner model supports three-part banners."""
        banner = Banners(
            name="three_part",
            left_image="left.png",
            middle_image="middle.png",
            right_image="right.png",
        )
        assert banner.left_image == "left.png"
        assert banner.middle_image == "middle.png"
        assert banner.right_image == "right.png"
        assert banner.full_image is None

    def test_banner_allows_invalid_layout_in_db_model(self):
        """DB model should allow rows; validation happens in schema/service."""
        banner = Banners(name="invalid", left_image="left.png")
        assert banner.left_image == "left.png"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_banner_model.py -v
```

Expected: FAIL with `ImportError: cannot import name 'BannerSize' from 'app.models.misc'`

**Step 3: Update the Banner model**

Replace the entire Banners section in `app/models/misc.py` (lines 18-55) with:

```python
# ===== Banners =====

from enum import Enum


class BannerSize(str, Enum):
    """Banner size variants."""

    small = "small"
    medium = "medium"
    large = "large"


class BannerBase(SQLModel):
    """
    Base model with shared public fields for Banners.

    These fields are safe to expose via the API.
    """

    name: str = Field(max_length=255)
    author: str | None = Field(default=None, max_length=255)
    size: BannerSize = Field(default=BannerSize.medium)

    # Image paths (relative to banner directory)
    full_image: str | None = Field(default=None, max_length=255)
    left_image: str | None = Field(default=None, max_length=255)
    middle_image: str | None = Field(default=None, max_length=255)
    right_image: str | None = Field(default=None, max_length=255)

    # Theme compatibility
    supports_dark: bool = Field(default=True)
    supports_light: bool = Field(default=True)

    # State
    active: bool = Field(default=True)


class Banners(BannerBase, table=True):
    """
    Database table for site banners.

    Banners are displayed at the top of the site and can be themed or event-specific.
    Supports both full-width banners (single image) and three-part banners
    (left + middle + right images).
    """

    __tablename__ = "banners"

    # Primary key
    banner_id: int | None = Field(default=None, primary_key=True)

    # Timestamp
    created_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_banner_model.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add app/models/misc.py tests/unit/test_banner_model.py
git commit -m "feat(banners): update Banner model with size and theme support"
```

---

## Task 3: Create Banner Schema (with layout validation)

**Files:**
- Create: `app/schemas/banner.py`
- Modify: `app/schemas/__init__.py`

**Step 1: Write failing test for BannerResponse schema**

Create `tests/unit/test_banner_schema.py`:

```python
"""Tests for Banner schemas."""

import pytest
from app.schemas.banner import BannerResponse
from app.models.misc import BannerSize


class TestBannerResponse:
    """Tests for BannerResponse schema."""

    def test_full_banner_response(self):
        """Test BannerResponse with full banner."""
        response = BannerResponse(
            banner_id=1,
            name="test_banner",
            author="artist",
            size=BannerSize.medium,
            full_image="test.png",
            left_image=None,
            middle_image=None,
            right_image=None,
            supports_dark=True,
            supports_light=True,
        )
        assert response.is_full is True
        assert response.full_image_url == "/static/banners/test.png"
        assert response.left_image_url is None

    def test_three_part_banner_response(self):
        """Test BannerResponse with three-part banner."""
        response = BannerResponse(
            banner_id=2,
            name="three_part",
            author=None,
            size=BannerSize.large,
            full_image=None,
            left_image="left.png",
            middle_image="middle.png",
            right_image="right.png",
            supports_dark=True,
            supports_light=False,
        )
        assert response.is_full is False
        assert response.full_image_url is None
        assert response.left_image_url == "/static/banners/left.png"
        assert response.middle_image_url == "/static/banners/middle.png"
        assert response.right_image_url == "/static/banners/right.png"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_banner_schema.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.banner'`

**Step 3: Create the banner schema**

Create `app/schemas/banner.py`:

```python
"""
Banner API schemas.

Defines request/response models for banner endpoints.
"""

from pydantic import BaseModel, computed_field, model_validator

from app.config import settings
from app.models.misc import BannerSize


class BannerResponse(BaseModel):
    """Response schema for banner data."""

    banner_id: int
    name: str
    author: str | None
    size: BannerSize
    supports_dark: bool
    supports_light: bool

    # Raw image paths from database
    full_image: str | None
    left_image: str | None
    middle_image: str | None
    right_image: str | None

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_full(self) -> bool:
        """True if this is a full-width banner, False if three-part."""
        return self.full_image is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_image_url(self) -> str | None:
        """Computed full URL for full banner image."""
        if self.full_image:
            return f"{settings.BANNER_BASE_URL}/{self.full_image}"
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def left_image_url(self) -> str | None:
        """Computed full URL for left banner image."""
        if self.left_image:
            return f"{settings.BANNER_BASE_URL}/{self.left_image}"
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def middle_image_url(self) -> str | None:
        """Computed full URL for middle banner image."""
        if self.middle_image:
            return f"{settings.BANNER_BASE_URL}/{self.middle_image}"
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def right_image_url(self) -> str | None:
        """Computed full URL for right banner image."""
        if self.right_image:
            return f"{settings.BANNER_BASE_URL}/{self.right_image}"
        return None

    @model_validator(mode="after")
    def _validate_layout(self):
        """Ensure banner is either full-image OR three-part (all parts present)."""
        has_full = self.full_image is not None
        parts = [self.left_image, self.middle_image, self.right_image]
        has_any_part = any(p is not None for p in parts)
        has_all_parts = all(p is not None for p in parts)

        if has_full and has_any_part:
            raise ValueError("Banner cannot have both full_image and three-part images")
        if not has_full and not has_any_part:
            raise ValueError("Banner must have either full_image or three-part images")
        if has_any_part and not has_all_parts:
            raise ValueError("Three-part banner must include left_image, middle_image, and right_image")
        return self
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_banner_schema.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/banner.py tests/unit/test_banner_schema.py
git commit -m "feat(banners): add BannerResponse schema with computed URLs"
```

---

## Task 4: Create Banner Service

**Files:**
- Create: `app/services/banner.py`

**Step 1: Write failing integration test for banner service (real Redis + real DB)**

Because the service behavior is fundamentally “DB + Redis + schema validation”, avoid heavy mocking.

Create `tests/integration/test_banner_service.py`:

```python
"""Integration tests for Banner service."""

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize
from app.services.banner import BANNER_CACHE_KEY_PREFIX, get_current_banner


@pytest.mark.integration
class TestGetCurrentBanner:
    async def test_cache_hit_returns_cached(self, db_session: AsyncSession, redis_client: redis.Redis):  # type: ignore[type-arg]
        # Seed cache directly
        cache_key = f"{BANNER_CACHE_KEY_PREFIX}dark:medium"
        await redis_client.set(cache_key, '{"banner_id":1,"name":"cached","author":null,"size":"medium","supports_dark":true,"supports_light":true,"full_image":"x.png","left_image":null,"middle_image":null,"right_image":null}')

        result = await get_current_banner("dark", "medium", db_session, redis_client)
        assert result.banner_id == 1
        assert result.name == "cached"

    async def test_cache_miss_selects_valid_banner_and_sets_cache(self, db_session: AsyncSession, redis_client: redis.Redis):  # type: ignore[type-arg]
        banner = Banners(
            name="db_banner",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=True,
            full_image="db.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        result = await get_current_banner("dark", "medium", db_session, redis_client)
        assert result.name == "db_banner"
        assert await redis_client.exists(f"{BANNER_CACHE_KEY_PREFIX}dark:medium")

    async def test_invalid_layout_rows_are_ignored(self, db_session: AsyncSession, redis_client: redis.Redis):  # type: ignore[type-arg]
        # Invalid: only one part set
        db_session.add(Banners(name="bad", size=BannerSize.medium, supports_dark=True, supports_light=True, left_image="left.png", active=True))
        # Valid: full
        db_session.add(Banners(name="good", size=BannerSize.medium, supports_dark=True, supports_light=True, full_image="ok.png", active=True))
        await db_session.commit()

        result = await get_current_banner("dark", "medium", db_session, redis_client)
        assert result.name == "good"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_banner_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.banner'`

**Step 3: Create the banner service**

Create `app/services/banner.py`:

```python
"""
Banner service for caching and retrieval.

Handles Redis caching of randomly selected banners with theme and size filtering.
"""

import json
import random

import redis.asyncio as redis
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.misc import Banners, BannerSize
from app.schemas.banner import BannerResponse

BANNER_CACHE_KEY_PREFIX = "banner:current:"


def _make_cache_key(theme: str, size: str) -> str:
    """Generate Redis cache key for banner."""
    return f"{BANNER_CACHE_KEY_PREFIX}{theme}:{size}"


async def get_current_banner(
    theme: str,
    size: str,
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
) -> BannerResponse:
    """
    Get the current banner for a theme and size.

    Checks Redis cache first. On cache miss, queries database for eligible
    banners, picks one randomly, caches it, and returns it.

    Args:
        theme: "dark" or "light"
        size: "small", "medium", or "large"
        db: Database session
        redis_client: Redis client

    Returns:
        BannerResponse with banner data

    Raises:
        HTTPException: 404 if no banners available for the theme/size combination
    """
    cache_key = _make_cache_key(theme, size)

    # Try cache first
    cached = await redis_client.get(cache_key)
    if cached:
        try:
            return BannerResponse.model_validate_json(cached)
        except Exception:
            # Cache corrupted/invalid schema/layout; fall through to DB.
            await redis_client.delete(cache_key)

    # Cache miss - query database
    theme_filter = Banners.supports_dark if theme == "dark" else Banners.supports_light
    query = select(Banners).where(
        Banners.active == True,  # noqa: E712
        theme_filter == True,  # noqa: E712
        Banners.size == size,
    )
    result = await db.execute(query)
    banners = result.scalars().all()

    if not banners:
        raise HTTPException(
            status_code=404,
            detail=f"No banners available for theme '{theme}' and size '{size}'",
        )

    # Filter out invalid banner layouts (schema validator enforces full vs 3-part)
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

    # Random selection
    response = random.choice(valid_responses)

    # Cache the response
    ttl = settings.BANNER_CACHE_TTL
    if settings.BANNER_CACHE_TTL_JITTER:
        ttl += random.randint(0, settings.BANNER_CACHE_TTL_JITTER)
    await redis_client.setex(cache_key, ttl, response.model_dump_json())

    return response


async def list_banners(
    db: AsyncSession,
    theme: str | None = None,
    size: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Banners], int]:
    """
    List active banners with optional filtering.

    Args:
        db: Database session
        theme: Optional theme filter ("dark" or "light")
        size: Optional size filter
        page: Page number (1-indexed)
        per_page: Items per page

    Returns:
        Tuple of (list of banners, total count)
    """
    # Base query - active banners only
    query = select(Banners).where(Banners.active == True)  # noqa: E712

    # Apply theme filter
    if theme == "dark":
        query = query.where(Banners.supports_dark == True)  # noqa: E712
    elif theme == "light":
        query = query.where(Banners.supports_light == True)  # noqa: E712

    # Apply size filter
    if size:
        query = query.where(Banners.size == size)

    # Get total count
    from sqlalchemy import func

    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    banners = list(result.scalars().all())

    return banners, total
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_banner_service.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add app/services/banner.py tests/integration/test_banner_service.py
git commit -m "feat(banners): add banner service with Redis caching"
```

**Note on Redis for tests:**

- There is already a real Redis fixture under `tests/unit/conftest.py`, but API/integration tests should not rely on `tests/unit/` fixtures.
- Add a top-level `redis_client` fixture in `tests/conftest.py` (or `tests/integration/conftest.py`) modeled after `tests/unit/conftest.py`, using `localhost:6379` and an isolated DB number (recommend `db=1`).
- For API tests that need real Redis, create a separate `app_real_redis` / `client_real_redis` fixture that overrides `get_redis` to yield the real client (instead of `mock_redis`).

---

## Task 5: Create Banner API Endpoints

**Files:**
- Create: `app/api/v1/banners.py`
- Modify: `app/api/v1/__init__.py`

**Step 1: Write failing API test**

Create `tests/api/v1/test_banners.py`:

```python
"""
Tests for banner API endpoints.

These tests cover the /api/v1/banners endpoints including:
- Get current banner (with caching)
- List banners
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize


@pytest.fixture
async def sample_banners(db_session: AsyncSession):
    """Create sample banners for testing."""
    banners = [
        Banners(
            name="dark_medium_banner",
            full_image="dark_medium.png",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=False,
            active=True,
        ),
        Banners(
            name="light_small_banner",
            full_image="light_small.png",
            size=BannerSize.small,
            supports_dark=False,
            supports_light=True,
            active=True,
        ),
        Banners(
            name="universal_large_banner",
            left_image="large_left.png",
            middle_image="large_middle.png",
            right_image="large_right.png",
            size=BannerSize.large,
            supports_dark=True,
            supports_light=True,
            active=True,
        ),
        Banners(
            name="inactive_banner",
            full_image="inactive.png",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=True,
            active=False,
        ),
    ]
    for banner in banners:
        db_session.add(banner)
    await db_session.commit()
    for banner in banners:
        await db_session.refresh(banner)
    return banners


@pytest.mark.api
class TestGetCurrentBanner:
    """Tests for GET /api/v1/banners/current endpoint."""

    async def test_get_current_banner_success(
        self, client: AsyncClient, sample_banners
    ):
        """Test getting current banner with valid theme and size."""
        response = await client.get(
            "/api/v1/banners/current",
            params={"theme": "dark", "size": "medium"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "dark_medium_banner"
        assert data["is_full"] is True
        assert "full_image_url" in data

    async def test_get_current_banner_no_match(self, client: AsyncClient, sample_banners):
        """Test 404 when no banners match theme/size."""
        response = await client.get(
            "/api/v1/banners/current",
            params={"theme": "light", "size": "large"},
        )
        # universal_large supports light, so this should work
        assert response.status_code == 200

    async def test_get_current_banner_truly_no_match(
        self, client: AsyncClient, sample_banners
    ):
        """Test 404 when truly no banners match."""
        response = await client.get(
            "/api/v1/banners/current",
            params={"theme": "dark", "size": "small"},
        )
        assert response.status_code == 404

    async def test_get_current_banner_invalid_theme(self, client: AsyncClient):
        """Test validation error for invalid theme."""
        response = await client.get(
            "/api/v1/banners/current",
            params={"theme": "invalid", "size": "medium"},
        )
        assert response.status_code == 422

    async def test_get_current_banner_missing_params(self, client: AsyncClient):
        """Test validation error for missing required params."""
        response = await client.get("/api/v1/banners/current")
        assert response.status_code == 422


@pytest.mark.api
class TestListBanners:
    """Tests for GET /api/v1/banners endpoint."""

    async def test_list_banners_returns_active_only(
        self, client: AsyncClient, sample_banners
    ):
        """Test that only active banners are returned."""
        response = await client.get("/api/v1/banners")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "total" in data
        # 3 active banners (inactive should not be included)
        assert data["total"] == 3
        assert len(data["items"]) == 3

    async def test_list_banners_filter_by_theme(
        self, client: AsyncClient, sample_banners
    ):
        """Test filtering banners by theme."""
        response = await client.get("/api/v1/banners", params={"theme": "dark"})
        assert response.status_code == 200

        data = response.json()
        # dark_medium and universal_large support dark
        assert data["total"] == 2

    async def test_list_banners_filter_by_size(
        self, client: AsyncClient, sample_banners
    ):
        """Test filtering banners by size."""
        response = await client.get("/api/v1/banners", params={"size": "small"})
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "light_small_banner"

    async def test_list_banners_pagination(self, client: AsyncClient, sample_banners):
        """Test banner list pagination."""
        response = await client.get(
            "/api/v1/banners", params={"page": 1, "per_page": 2}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/v1/test_banners.py -v
```

Expected: FAIL with 404 (endpoint not found)

**Step 3: Create the banner API router**

Create `app/api/v1/banners.py`:

```python
"""
Banner API endpoints.

Provides endpoints for retrieving site banners with caching.
"""

from typing import Annotated, Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.misc import BannerSize
from app.schemas.banner import BannerResponse
from app.services.banner import get_current_banner, list_banners

router = APIRouter(prefix="/banners", tags=["banners"])


class BannerListResponse(BaseModel):
    """Paginated banner list response."""

    items: list[BannerResponse]
    total: int
    page: int
    per_page: int


@router.get("/current", response_model=BannerResponse)
async def get_banner(
    theme: Annotated[Literal["dark", "light"], Query(description="Theme mode")],
    size: Annotated[
        Literal["small", "medium", "large"], Query(description="Banner size")
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> BannerResponse:
    """
    Get the current rotating banner.

    Returns a randomly selected banner that matches the specified theme and size.
    The selection is cached for 10 minutes, so all users see the same banner
    during that period.

    Args:
        theme: User's theme mode (dark or light)
        size: Desired banner size

    Returns:
        Banner data including image URLs
    """
    return await get_current_banner(theme, size, db, redis_client)


@router.get("", response_model=BannerListResponse)
async def list_all_banners(
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    theme: Annotated[
        Literal["dark", "light"] | None,
        Query(description="Filter by theme compatibility"),
    ] = None,
    size: Annotated[
        Literal["small", "medium", "large"] | None,
        Query(description="Filter by size"),
    ] = None,
) -> BannerListResponse:
    """
    List all active banners.

    Returns paginated list of active banners with optional filtering.
    Useful for displaying available banners or for future user preferences.

    Args:
        theme: Optional filter by theme compatibility
        size: Optional filter by size
        pagination: Pagination parameters

    Returns:
        Paginated list of banners
    """
    banners, total = await list_banners(
        db,
        theme=theme,
        size=size,
        page=pagination.page,
        per_page=pagination.per_page,
    )

    return BannerListResponse(
        items=[BannerResponse.model_validate(b) for b in banners],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )
```

**Step 4: Register the router**

In `app/api/v1/__init__.py`, add the import and include:

After line 17 (after `users` import), add:
```python
from app.api.v1 import banners
```

After line 35 (after `router.include_router(permissions.router)`), add:
```python
router.include_router(banners.router)
```

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/api/v1/test_banners.py -v
```

Expected: PASS

**Step 6: Commit**

```bash
git add app/api/v1/banners.py app/api/v1/__init__.py tests/api/v1/test_banners.py
git commit -m "feat(banners): add banner API endpoints"
```

---

## Task 6: Create Database Migration

**Files:**
- Create: `alembic/versions/xxxx_recreate_banners_table.py`

**Step 1: Generate migration**

```bash
uv run alembic revision -m "recreate_banners_table"
```

Note the generated filename.

**Step 2: Edit the migration file**

Replace the content with:

```python
"""recreate_banners_table

Revision ID: <auto-generated>
Revises: 9c92a1686d79
Create Date: <auto-generated>

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '<auto-generated>'
down_revision: str | Sequence[str] | None = '9c92a1686d79'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop old banners table
    op.drop_table('banners')

    # Create new banners table with updated schema
    op.create_table(
        'banners',
        sa.Column('banner_id', mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('author', sa.String(255), nullable=True),
        sa.Column('size', sa.Enum('small', 'medium', 'large', name='bannersize'), nullable=False, server_default='medium'),
        sa.Column('full_image', sa.String(255), nullable=True),
        sa.Column('left_image', sa.String(255), nullable=True),
        sa.Column('middle_image', sa.String(255), nullable=True),
        sa.Column('right_image', sa.String(255), nullable=True),
        sa.Column('supports_dark', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('supports_light', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=True),
        sa.PrimaryKeyConstraint('banner_id'),
    )

    # Create indexes for efficient querying
    op.create_index('idx_banners_active_dark_size', 'banners', ['active', 'supports_dark', 'size'])
    op.create_index('idx_banners_active_light_size', 'banners', ['active', 'supports_light', 'size'])


def downgrade() -> None:
    # Drop new indexes
    op.drop_index('idx_banners_active_light_size', table_name='banners')
    op.drop_index('idx_banners_active_dark_size', table_name='banners')

    # Drop new table
    op.drop_table('banners')

    # Recreate old banners table (legacy schema)
    op.create_table(
        'banners',
        sa.Column('banner_id', mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column('path', sa.String(255), nullable=False, server_default=''),
        sa.Column('author', sa.String(255), nullable=False, server_default=''),
        sa.Column('leftext', sa.String(3), nullable=False, server_default='png'),
        sa.Column('midext', sa.String(3), nullable=False, server_default='png'),
        sa.Column('rightext', sa.String(3), nullable=False, server_default='png'),
        sa.Column('full', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('event_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('date', sa.DateTime(), server_default=sa.text('current_timestamp()'), nullable=True),
        sa.PrimaryKeyConstraint('banner_id'),
    )
```

**Step 3: Run the migration**

```bash
uv run alembic upgrade head
```

Expected: Migration completes successfully.

**Step 4: Verify migration**

```bash
uv run alembic current
```

Expected: Shows the new revision as current.

**Step 5: Commit**

```bash
git add alembic/versions/*_recreate_banners_table.py
git commit -m "feat(banners): add migration to recreate banners table"
```

---

## Task 7: Update Test Fixtures

**Files:**
- Modify: `tests/conftest.py`

**Step 1: Verify model import in conftest.py**

The Banners model is already imported in `tests/conftest.py` (line 251-257). No changes needed since we're modifying the existing model, not creating a new one.

**Step 2: Run full test suite**

```bash
uv run pytest tests/ -v --ignore=tests/integration
```

Expected: All tests pass.

**Step 3: Commit if any changes were needed**

```bash
git status
# If conftest.py was modified:
git add tests/conftest.py
git commit -m "test: update conftest for banner model changes"
```

---

## Task 8: Manual Integration Testing

**Step 1: Start services**

```bash
docker compose up -d
```

**Step 2: Create test banner via database**

```bash
docker compose exec mariadb mysql -u shuushuu -pshuushuu_password shuushuu -e "
INSERT INTO banners (name, full_image, size, supports_dark, supports_light, active)
VALUES ('test_dark_medium', 'test.png', 'medium', 1, 0, 1);
INSERT INTO banners (name, full_image, size, supports_dark, supports_light, active)
VALUES ('test_light_medium', 'light.png', 'medium', 0, 1, 1);
INSERT INTO banners (name, left_image, middle_image, right_image, size, supports_dark, supports_light, active)
VALUES ('test_universal_large', 'left.png', 'mid.png', 'right.png', 'large', 1, 1, 1);
"
```

**Step 3: Test endpoints**

```bash
# Test get current banner
curl -s "http://localhost:8000/api/v1/banners/current?theme=dark&size=medium" | jq

# Test list banners
curl -s "http://localhost:8000/api/v1/banners" | jq

# Test filtered list
curl -s "http://localhost:8000/api/v1/banners?theme=dark" | jq
```

**Step 4: Verify caching**

```bash
# First request should hit DB
curl -s "http://localhost:8000/api/v1/banners/current?theme=dark&size=medium" | jq

# Check Redis for cached value
docker compose exec redis redis-cli GET "banner:current:dark:medium"
```

**Step 5: Clean up test data**

```bash
docker compose exec mariadb mysql -u shuushuu -pshuushuu_password shuushuu -e "DELETE FROM banners;"
```

---

## Task 9: Final Verification and PR

**Step 1: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

**Step 2: Run linting**

```bash
uv run ruff check app/ tests/
uv run ruff format app/ tests/
```

**Step 3: Run type checking**

```bash
uv run mypy app/
```

**Step 4: Create final commit if needed**

```bash
git status
# Add any remaining files
git add -A
git commit -m "chore: final cleanup for banner feature"
```

**Step 5: Push and create PR**

```bash
git push -u origin HEAD
gh pr create --title "feat: add rotating banner system" --body "$(cat <<'EOF'
## Summary
- Add rotating banner system with Redis caching
- Support theme compatibility (dark/light) and size variants (small/medium/large)
- Support both full-width and three-part banners
- Add GET /api/v1/banners/current endpoint with 10-minute cache
- Add GET /api/v1/banners endpoint to list active banners

## Test plan
- [x] Unit tests for model, schema, and service
- [x] API tests for both endpoints
- [x] Manual integration testing
- [ ] Frontend integration (separate PR)

Implements design from docs/plans/2026-02-01-rotating-banners-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add config settings | `app/config.py` |
| 2 | Update Banner model | `app/models/misc.py`, `tests/unit/test_banner_model.py` |
| 3 | Create Banner schema | `app/schemas/banner.py`, `tests/unit/test_banner_schema.py` |
| 4 | Create Banner service | `app/services/banner.py`, `tests/unit/test_banner_service.py` |
| 5 | Create API endpoints | `app/api/v1/banners.py`, `app/api/v1/__init__.py`, `tests/api/v1/test_banners.py` |
| 6 | Database migration | `alembic/versions/*_recreate_banners_table.py` |
| 7 | Update test fixtures | `tests/conftest.py` (if needed) |
| 8 | Manual integration testing | N/A |
| 9 | Final verification and PR | N/A |

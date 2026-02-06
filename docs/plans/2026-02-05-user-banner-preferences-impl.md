# User Banner Preferences Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow logged-in users to choose a preferred banner size and pin favorite banners per size+theme slot, with server-side resolution in the existing `/current` endpoint.

**Architecture:** Two new tables (`user_banner_preferences`, `user_banner_pins`) following the existing SQLModel inheritance pattern in `app/models/misc.py`. New service functions for preference CRUD. The existing `get_current_banner` service function gains an optional `user_id` parameter for server-side resolution. New API endpoints for preference management; the existing `/current` endpoint gains optional auth via `OptionalCurrentUser`.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, MariaDB, Redis (existing caching unchanged), pytest

**Reference files:**
- Design: `docs/plans/2026-02-05-user-banner-preferences-design.md`
- Existing models: `app/models/misc.py` (BannerSize, BannerBase, Banners)
- Existing schemas: `app/schemas/banner.py` (BannerResponse, BannerListResponse)
- Existing service: `app/services/banner.py` (get_current_banner, list_banners)
- Existing routes: `app/api/v1/banners.py`
- Auth dependencies: `app/core/auth.py` (OptionalCurrentUser, CurrentUser, get_optional_current_user)
- Test fixtures: `tests/conftest.py` (authenticated_client, sample_user, client_real_redis, db_session)
- Existing banner tests: `tests/api/v1/test_banners.py`, `tests/integration/test_banner_service.py`

---

### Task 1: Add models — UserBannerPreferences and UserBannerPins

**Files:**
- Modify: `app/models/misc.py` (add after line 71, the Banners class)
- Modify: `tests/conftest.py` (add imports for new models in `setup_test_database`)

**Step 1: Add `BannerTheme` enum and both models to `app/models/misc.py`**

Add after the `Banners` class (line 71):

```python
class BannerTheme(str, Enum):
    """Banner theme variants."""

    dark = "dark"
    light = "light"


class UserBannerPreferencesBase(SQLModel):
    """Base model for user banner size preference."""

    preferred_size: BannerSize = Field(default=BannerSize.small)


class UserBannerPreferences(UserBannerPreferencesBase, table=True):
    """One row per user — stores preferred banner size."""

    __tablename__ = "user_banner_preferences"

    user_id: int = Field(primary_key=True, foreign_key="users.user_id")


class UserBannerPinsBase(SQLModel):
    """Base model for user banner pins."""

    size: BannerSize
    theme: BannerTheme


class UserBannerPins(UserBannerPinsBase, table=True):
    """One row per pin — up to 6 per user (3 sizes x 2 themes)."""

    __tablename__ = "user_banner_pins"

    __table_args__ = (
        Index("uq_user_size_theme", "user_id", "size", "theme", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.user_id")
    banner_id: int = Field(foreign_key="banners.banner_id")
```

**Step 2: Add imports in `tests/conftest.py`**

In the `setup_test_database` fixture, find the existing misc imports block (around line 251) and add the new models:

```python
from app.models.misc import (  # noqa: F401
    Banners,
    Donations,
    ImageRatingsAvg,
    Quicklinks,
    Tips,
    UserBannerPins,
    UserBannerPreferences,
)
```

**Step 3: Run tests to verify models don't break anything**

Run: `uv run pytest tests/unit/test_banner_model.py -v`
Expected: All existing tests PASS

**Step 4: Commit**

```bash
git add app/models/misc.py tests/conftest.py
git commit -m "feat: add UserBannerPreferences and UserBannerPins models"
```

---

### Task 2: Add unit tests for new models

**Files:**
- Modify: `tests/unit/test_banner_model.py` (add tests for new models)

**Step 1: Write failing tests**

Add to `tests/unit/test_banner_model.py`:

```python
from app.models.misc import BannerTheme, UserBannerPins, UserBannerPreferences


class TestBannerThemeEnum:
    def test_values(self):
        assert BannerTheme.dark == "dark"
        assert BannerTheme.light == "light"

    def test_has_exactly_two_values(self):
        assert len(BannerTheme) == 2


class TestUserBannerPreferencesModel:
    def test_default_preferred_size(self):
        prefs = UserBannerPreferences(user_id=1)
        assert prefs.preferred_size == BannerSize.small

    def test_custom_preferred_size(self):
        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        assert prefs.preferred_size == BannerSize.large

    def test_table_name(self):
        assert UserBannerPreferences.__tablename__ == "user_banner_preferences"


class TestUserBannerPinsModel:
    def test_fields(self):
        pin = UserBannerPins(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=10,
        )
        assert pin.user_id == 1
        assert pin.size == BannerSize.small
        assert pin.theme == BannerTheme.dark
        assert pin.banner_id == 10

    def test_table_name(self):
        assert UserBannerPins.__tablename__ == "user_banner_pins"
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_banner_model.py -v`
Expected: All tests PASS (these test model construction, not DB)

**Step 3: Commit**

```bash
git add tests/unit/test_banner_model.py
git commit -m "test: add unit tests for UserBannerPreferences and UserBannerPins models"
```

---

### Task 3: Add database migration

**Files:**
- Create: `alembic/versions/xxx_add_user_banner_preferences.py`

**Step 1: Create the migration**

Run: `uv run alembic revision -m "add user banner preferences"`

**Step 2: Edit the migration file**

Write the upgrade/downgrade functions. Reference the SQL from the design doc (`docs/plans/2026-02-05-user-banner-preferences-design.md`, lines 42-61):

```python
"""add user banner preferences

Revision ID: <auto-generated>
"""
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "user_banner_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "preferred_size",
            sa.Enum("small", "medium", "large", name="bannersize"),
            nullable=False,
            server_default="small",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    op.create_table(
        "user_banner_pins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "size",
            sa.Enum("small", "medium", "large", name="bannersize"),
            nullable=False,
        ),
        sa.Column("theme", sa.VARCHAR(5), nullable=False),
        sa.Column("banner_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["banner_id"],
            ["banners.banner_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    op.create_index(
        "uq_user_size_theme",
        "user_banner_pins",
        ["user_id", "size", "theme"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("user_banner_pins")
    op.drop_table("user_banner_preferences")
```

**Step 3: Verify migration applies**

Run: `uv run alembic upgrade head`
Expected: Migration applies without errors

**Step 4: Run all existing tests to verify nothing broke**

Run: `uv run pytest tests/unit/test_banner_model.py tests/unit/test_banner_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add alembic/versions/*add_user_banner_preferences*
git commit -m "migration: add user_banner_preferences and user_banner_pins tables"
```

---

### Task 4: Add schemas for preferences API

**Files:**
- Modify: `app/schemas/banner.py` (add preference/pin schemas)

**Step 1: Write failing unit test for schemas**

Create `tests/unit/test_banner_preferences_schema.py`:

```python
"""Tests for banner preference schemas."""

import pytest
from pydantic import ValidationError

from app.models.misc import BannerSize, BannerTheme
from app.schemas.banner import (
    BannerPinResponse,
    BannerPreferencesResponse,
    PinRequest,
    PreferenceUpdateRequest,
)


class TestPreferenceUpdateRequest:
    def test_valid_size(self):
        req = PreferenceUpdateRequest(preferred_size=BannerSize.large)
        assert req.preferred_size == BannerSize.large

    def test_rejects_invalid_size(self):
        with pytest.raises(ValidationError):
            PreferenceUpdateRequest(preferred_size="huge")


class TestPinRequest:
    def test_valid_banner_id(self):
        req = PinRequest(banner_id=42)
        assert req.banner_id == 42

    def test_rejects_missing_banner_id(self):
        with pytest.raises(ValidationError):
            PinRequest()


class TestBannerPinResponse:
    def test_fields(self):
        pin = BannerPinResponse(
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner=None,
        )
        assert pin.size == BannerSize.small
        assert pin.theme == BannerTheme.dark
        assert pin.banner is None


class TestBannerPreferencesResponse:
    def test_defaults(self):
        resp = BannerPreferencesResponse(
            preferred_size=BannerSize.small,
            pins=[],
        )
        assert resp.preferred_size == BannerSize.small
        assert resp.pins == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_banner_preferences_schema.py -v`
Expected: FAIL with ImportError (schemas don't exist yet)

**Step 3: Add schemas to `app/schemas/banner.py`**

Add at the end of the file:

```python
from app.models.misc import BannerTheme


class PreferenceUpdateRequest(BaseModel):
    """Request to update banner size preference."""

    preferred_size: BannerSize


class PinRequest(BaseModel):
    """Request to pin a banner for a size+theme slot."""

    banner_id: int


class BannerPinResponse(BaseModel):
    """A single pin entry in the preferences response."""

    size: BannerSize
    theme: BannerTheme
    banner: BannerResponse | None


class BannerPreferencesResponse(BaseModel):
    """Full user banner preferences response."""

    preferred_size: BannerSize
    pins: list[BannerPinResponse]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_banner_preferences_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/schemas/banner.py tests/unit/test_banner_preferences_schema.py
git commit -m "feat: add banner preference request/response schemas"
```

---

### Task 5: Add preference service functions

**Files:**
- Modify: `app/services/banner.py` (add preference CRUD functions)

**Step 1: Write failing integration tests**

Create `tests/integration/test_banner_preferences_service.py`:

```python
"""Integration tests for banner preference service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize, BannerTheme, UserBannerPins, UserBannerPreferences
from app.schemas.banner import BannerPreferencesResponse


@pytest.mark.integration
class TestGetUserPreferences:
    async def test_returns_defaults_when_no_row(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        result = await get_user_preferences(user_id=1, db=db_session)
        assert result.preferred_size == BannerSize.small
        assert result.pins == []

    async def test_returns_stored_preferences(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        db_session.add(prefs)
        await db_session.commit()

        result = await get_user_preferences(user_id=1, db=db_session)
        assert result.preferred_size == BannerSize.large

    async def test_returns_pins_with_banner_data(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        banner = Banners(
            name="pinned",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="pin.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_user_preferences(user_id=1, db=db_session)
        assert len(result.pins) == 1
        assert result.pins[0].size == BannerSize.small
        assert result.pins[0].theme == BannerTheme.dark
        assert result.pins[0].banner is not None
        assert result.pins[0].banner.name == "pinned"


@pytest.mark.integration
class TestUpdatePreferredSize:
    async def test_creates_row_if_not_exists(self, db_session: AsyncSession):
        from app.services.banner import update_preferred_size

        await update_preferred_size(user_id=1, size=BannerSize.large, db=db_session)

        result = await db_session.get(UserBannerPreferences, 1)
        assert result is not None
        assert result.preferred_size == BannerSize.large

    async def test_updates_existing_row(self, db_session: AsyncSession):
        from app.services.banner import update_preferred_size

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.small)
        db_session.add(prefs)
        await db_session.commit()

        await update_preferred_size(user_id=1, size=BannerSize.medium, db=db_session)

        await db_session.refresh(prefs)
        assert prefs.preferred_size == BannerSize.medium


@pytest.mark.integration
class TestPinBanner:
    async def test_creates_pin(self, db_session: AsyncSession):
        from app.services.banner import pin_banner

        banner = Banners(
            name="pinme",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="pin.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        await pin_banner(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner.banner_id,
            db=db_session,
        )

        from sqlalchemy import select
        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        pin = result.scalar_one()
        assert pin.banner_id == banner.banner_id

    async def test_rejects_nonexistent_banner(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=99999,
                db=db_session,
            )
        assert exc_info.value.status_code == 404

    async def test_rejects_inactive_banner(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="inactive",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="x.png",
            active=False,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_size_mismatch(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="medium_banner",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=True,
            full_image="m.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_theme_mismatch(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="light_only",
            size=BannerSize.small,
            supports_dark=False,
            supports_light=True,
            full_image="l.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_upserts_existing_pin(self, db_session: AsyncSession):
        from app.services.banner import pin_banner

        banner1 = Banners(
            name="first", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="1.png", active=True,
        )
        banner2 = Banners(
            name="second", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="2.png", active=True,
        )
        db_session.add_all([banner1, banner2])
        await db_session.commit()
        await db_session.refresh(banner1)
        await db_session.refresh(banner2)

        await pin_banner(user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
                         banner_id=banner1.banner_id, db=db_session)
        await pin_banner(user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
                         banner_id=banner2.banner_id, db=db_session)

        from sqlalchemy import select
        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        pins = result.scalars().all()
        assert len(pins) == 1
        assert pins[0].banner_id == banner2.banner_id


@pytest.mark.integration
class TestUnpinBanner:
    async def test_removes_pin(self, db_session: AsyncSession):
        from app.services.banner import unpin_banner

        banner = Banners(
            name="unpin_me", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="u.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
            banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        await unpin_banner(user_id=1, size=BannerSize.small, theme=BannerTheme.dark, db=db_session)

        from sqlalchemy import select
        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_404_when_no_pin(self, db_session: AsyncSession):
        from app.services.banner import unpin_banner
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await unpin_banner(user_id=1, size=BannerSize.small, theme=BannerTheme.dark, db=db_session)
        assert exc_info.value.status_code == 404
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_banner_preferences_service.py -v`
Expected: FAIL with ImportError

**Step 3: Implement service functions in `app/services/banner.py`**

Add the following imports at the top of the file:

```python
from app.models.misc import UserBannerPins, UserBannerPreferences, BannerTheme
from app.schemas.banner import BannerPinResponse, BannerPreferencesResponse
```

Add the following functions at the end of the file:

```python
async def get_user_preferences(
    user_id: int,
    db: AsyncSession,
) -> BannerPreferencesResponse:
    """Get a user's banner preferences, returning defaults if no row exists."""

    prefs = await db.get(UserBannerPreferences, user_id)
    preferred_size = prefs.preferred_size if prefs else BannerSize.small

    # Fetch all pins for this user with their banners
    pin_query = select(UserBannerPins).where(UserBannerPins.user_id == user_id)
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
                pass
        pins.append(BannerPinResponse(
            size=pin_row.size,
            theme=pin_row.theme,
            banner=banner_response,
        ))

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
        UserBannerPins.user_id == user_id,
        UserBannerPins.size == size,
        UserBannerPins.theme == theme,
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
        UserBannerPins.user_id == user_id,
        UserBannerPins.size == size,
        UserBannerPins.theme == theme,
    )
    result = await db.execute(query)
    pin = result.scalar_one_or_none()

    if not pin:
        raise HTTPException(status_code=404, detail="No pin found for this slot")

    await db.delete(pin)
    await db.commit()
```

**Step 4: Run integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_banner_preferences_service.py -v`
Expected: All PASS

**Step 5: Run all existing banner tests to confirm no regressions**

Run: `uv run pytest tests/unit/test_banner_model.py tests/unit/test_banner_schema.py tests/integration/test_banner_service.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add app/services/banner.py tests/integration/test_banner_preferences_service.py
git commit -m "feat: add banner preference service functions (CRUD, validation)"
```

---

### Task 6: Modify `get_current_banner` for server-side resolution

**Files:**
- Modify: `app/services/banner.py` (add optional user_id parameter to get_current_banner)

**Step 1: Write failing integration tests**

Add to `tests/integration/test_banner_preferences_service.py`:

```python
import json
import redis.asyncio as redis


@pytest.mark.integration
class TestGetCurrentBannerWithPreferences:
    async def test_anonymous_uses_query_param_size(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Anonymous user: size comes from query param, not preferences."""
        from app.services.banner import get_current_banner

        banner = Banners(
            name="medium_banner", size=BannerSize.medium, supports_dark=True,
            supports_light=True, full_image="m.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        result = await get_current_banner("dark", "medium", db_session, redis_client)
        assert result.size == BannerSize.medium

    async def test_authenticated_user_preferred_size_overrides_param(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Authenticated user with preferred_size=large gets large banners."""
        from app.services.banner import get_current_banner

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        db_session.add(prefs)

        banner = Banners(
            name="large_banner", size=BannerSize.large, supports_dark=True,
            supports_light=True, full_image="lg.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        # Pass user_id=1; the size param "small" should be overridden by preferred_size
        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.size == BannerSize.large

    async def test_authenticated_user_with_pin_returns_pinned(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Authenticated user with a pin for the effective size+theme gets pinned banner."""
        from app.services.banner import get_current_banner

        pinned = Banners(
            name="my_fave", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="fave.png", active=True,
        )
        other = Banners(
            name="other", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="other.png", active=True,
        )
        db_session.add_all([pinned, other])
        await db_session.commit()
        await db_session.refresh(pinned)

        pin = UserBannerPins(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
            banner_id=pinned.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.banner_id == pinned.banner_id
        assert result.name == "my_fave"

    async def test_pinned_inactive_banner_falls_through(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Pin on inactive banner falls through to normal rotation."""
        from app.services.banner import get_current_banner

        inactive = Banners(
            name="inactive_pinned", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="inactive.png", active=False,
        )
        fallback = Banners(
            name="fallback", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="fallback.png", active=True,
        )
        db_session.add_all([inactive, fallback])
        await db_session.commit()
        await db_session.refresh(inactive)

        pin = UserBannerPins(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
            banner_id=inactive.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.name == "fallback"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_banner_preferences_service.py::TestGetCurrentBannerWithPreferences -v`
Expected: FAIL (get_current_banner doesn't accept user_id yet)

**Step 3: Modify `get_current_banner` in `app/services/banner.py`**

Update the function signature to accept an optional `user_id`:

```python
async def get_current_banner(
    theme: str,
    size: str,
    db: AsyncSession,
    redis_client: redis.Redis,  # type: ignore[type-arg]
    user_id: int | None = None,
) -> BannerResponse:
```

Add preference resolution at the start of the function body, before the cache lookup:

```python
    effective_size = size

    if user_id is not None:
        # Check for user preferences
        prefs = await db.get(UserBannerPreferences, user_id)
        if prefs:
            effective_size = prefs.preferred_size.value

        # Check for pinned banner
        pin_query = select(UserBannerPins).where(
            UserBannerPins.user_id == user_id,
            UserBannerPins.size == BannerSize(effective_size),
            UserBannerPins.theme == BannerTheme(theme),
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
```

Then replace all remaining uses of `size` in the function with `effective_size` (the cache key, the size validation, the DB query filter).

**Step 4: Run integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_banner_preferences_service.py -v`
Expected: All PASS

**Step 5: Run existing banner tests to confirm no regressions**

Run: `uv run pytest tests/integration/test_banner_service.py tests/api/v1/test_banners.py -v`
Expected: All PASS (existing callers don't pass user_id, so default None preserves behavior)

**Step 6: Commit**

```bash
git add app/services/banner.py tests/integration/test_banner_preferences_service.py
git commit -m "feat: add user preference resolution to get_current_banner"
```

---

### Task 7: Add preference API endpoints

**Files:**
- Modify: `app/api/v1/banners.py` (add preference/pin endpoints, modify /current)

**Step 1: Write failing API tests**

Create `tests/api/v1/test_banner_preferences.py`:

```python
"""Tests for banner preference API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize, BannerTheme, UserBannerPins, UserBannerPreferences


@pytest.mark.api
class TestGetPreferences:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/v1/banners/preferences")
        assert response.status_code == 401

    async def test_returns_defaults(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        data = response.json()
        assert data["preferred_size"] == "small"
        assert data["pins"] == []

    async def test_returns_stored_preferences(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        prefs = UserBannerPreferences(user_id=sample_user.user_id, preferred_size=BannerSize.large)
        db_session.add(prefs)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        assert response.json()["preferred_size"] == "large"

    async def test_returns_pins_with_banner_data(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        banner = Banners(
            name="pinned", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="pin.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=sample_user.user_id, size=BannerSize.small,
            theme=BannerTheme.dark, banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        pins = response.json()["pins"]
        assert len(pins) == 1
        assert pins[0]["banner"]["name"] == "pinned"


@pytest.mark.api
class TestUpdatePreferences:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "large"},
        )
        assert response.status_code == 401

    async def test_updates_size(self, authenticated_client: AsyncClient):
        response = await authenticated_client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "large"},
        )
        assert response.status_code == 200
        assert response.json()["preferred_size"] == "large"

    async def test_rejects_invalid_size(self, authenticated_client: AsyncClient):
        response = await authenticated_client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "huge"},
        )
        assert response.status_code == 422


@pytest.mark.api
class TestPinBanner:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": 1},
        )
        assert response.status_code == 401

    async def test_pins_banner(
        self, authenticated_client: AsyncClient, db_session: AsyncSession,
    ):
        banner = Banners(
            name="to_pin", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="pin.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": banner.banner_id},
        )
        assert response.status_code == 200

    async def test_rejects_nonexistent_banner(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": 99999},
        )
        assert response.status_code == 404

    async def test_rejects_size_mismatch(
        self, authenticated_client: AsyncClient, db_session: AsyncSession,
    ):
        banner = Banners(
            name="medium_b", size=BannerSize.medium, supports_dark=True,
            supports_light=True, full_image="m.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": banner.banner_id},
        )
        assert response.status_code == 400

    async def test_rejects_invalid_size_path(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/huge/dark",
            json={"banner_id": 1},
        )
        assert response.status_code == 422

    async def test_rejects_invalid_theme_path(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/neon",
            json={"banner_id": 1},
        )
        assert response.status_code == 422


@pytest.mark.api
class TestUnpinBanner:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.delete("/api/v1/banners/preferences/pins/small/dark")
        assert response.status_code == 401

    async def test_removes_pin(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        banner = Banners(
            name="unpin_me", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="u.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=sample_user.user_id, size=BannerSize.small,
            theme=BannerTheme.dark, banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        response = await authenticated_client.delete(
            "/api/v1/banners/preferences/pins/small/dark",
        )
        assert response.status_code == 204

    async def test_404_when_no_pin(self, authenticated_client: AsyncClient):
        response = await authenticated_client.delete(
            "/api/v1/banners/preferences/pins/small/dark",
        )
        assert response.status_code == 404


@pytest.mark.api
class TestCurrentBannerWithAuth:
    async def test_anonymous_still_works(
        self, client_real_redis: AsyncClient, db_session: AsyncSession,
    ):
        """Anonymous request without auth works as before."""
        banner = Banners(
            name="anon", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="anon.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        response = await client_real_redis.get(
            "/api/v1/banners/current", params={"theme": "dark", "size": "small"},
        )
        assert response.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_banner_preferences.py -v`
Expected: FAIL (endpoints don't exist yet)

**Step 3: Implement the endpoints in `app/api/v1/banners.py`**

Update imports:

```python
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
```

Modify the `/current` endpoint to accept optional auth:

```python
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
```

Add the new endpoints (place them BEFORE the `list_active_banners` route so `/preferences` doesn't collide with the `GET /` catch-all):

```python
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
```

**Step 4: Run API tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_banner_preferences.py -v`
Expected: All PASS

**Step 5: Run ALL existing banner tests to confirm no regressions**

Run: `uv run pytest tests/api/v1/test_banners.py tests/unit/test_banner_model.py tests/unit/test_banner_schema.py tests/integration/test_banner_service.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add app/api/v1/banners.py tests/api/v1/test_banner_preferences.py
git commit -m "feat: add banner preference API endpoints and auth-aware /current"
```

---

### Task 8: Full test suite verification

**Files:** None (verification only)

**Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: All tests PASS, no regressions

**Step 2: Run type checking**

Run: `uv run mypy app/api/v1/banners.py app/services/banner.py app/models/misc.py app/schemas/banner.py`
Expected: No errors (or only pre-existing ones)

**Step 3: If all green, commit any remaining changes**

No commit expected unless fixups were needed.

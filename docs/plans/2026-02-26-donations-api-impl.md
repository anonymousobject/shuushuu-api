# Donations API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose donation data via three endpoints: list recent donations, monthly totals, and admin creation.

**Architecture:** Simple read-heavy endpoints on the existing `Donations` model (no joins needed). One write endpoint gated by a new `DONATIONS_CREATE` permission. Follows the same patterns as `news.py` and its tests.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async, Pydantic schemas, pytest + httpx

**Design doc:** `docs/plans/2026-02-26-donations-api-design.md`

---

### Task 1: Register Donations model and add permission

**Files:**
- Modify: `app/models/__init__.py` — add `Donations` import and `__all__` entry
- Modify: `app/core/permissions.py` — add `DONATIONS_CREATE` enum value and description

**Step 1: Add Donations to model registry**

In `app/models/__init__.py`, add to the misc imports:

```python
from app.models.misc import (
    Banners,
    Donations,
    EvaTheme,
    Quicklinks,
    Tips,
)
```

And add `"Donations"` to `__all__` in the "Utility models" section.

**Step 2: Add DONATIONS_CREATE permission**

In `app/core/permissions.py`, add to the `Permission` enum after the news section:

```python
    # Donation management
    DONATIONS_CREATE = "donations_create"
```

And add to `_PERMISSION_DESCRIPTIONS`:

```python
    # Donation management
    Permission.DONATIONS_CREATE: "Create donation records",
```

**Step 3: Verify the app starts**

Run: `uv run python -c "from app.models import Donations; from app.core.permissions import Permission; print(Permission.DONATIONS_CREATE)"`
Expected: `Permission.DONATIONS_CREATE`

**Step 4: Commit**

```bash
git add app/models/__init__.py app/core/permissions.py
git commit -m "feat: register Donations model and add DONATIONS_CREATE permission"
```

---

### Task 2: Create donation schemas

**Files:**
- Create: `app/schemas/donations.py`

**Step 1: Write unit tests for schemas**

Create `tests/unit/test_donation_schemas.py`:

```python
"""Tests for donation schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.donations import DonationCreate


class TestDonationCreate:
    """Validation tests for DonationCreate schema."""

    def test_valid_minimal(self):
        """Amount-only donation is valid."""
        d = DonationCreate(amount=10)
        assert d.amount == 10
        assert d.nick is None
        assert d.user_id is None
        assert d.date is None

    def test_valid_full(self):
        """All fields populated is valid."""
        d = DonationCreate(amount=50, nick="Donor", user_id=123)
        assert d.amount == 50
        assert d.nick == "Donor"
        assert d.user_id == 123

    def test_amount_required(self):
        """Missing amount raises validation error."""
        with pytest.raises(ValidationError):
            DonationCreate()

    def test_nick_max_length(self):
        """Nick over 30 chars raises validation error."""
        with pytest.raises(ValidationError):
            DonationCreate(amount=10, nick="a" * 31)

    def test_nick_strips_whitespace(self):
        """Nick is stripped of leading/trailing whitespace."""
        d = DonationCreate(amount=10, nick="  Donor  ")
        assert d.nick == "Donor"

    def test_amount_must_be_positive(self):
        """Amount must be greater than 0."""
        with pytest.raises(ValidationError):
            DonationCreate(amount=0)

        with pytest.raises(ValidationError):
            DonationCreate(amount=-5)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_donation_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.donations'`

**Step 3: Create the schemas**

Create `app/schemas/donations.py`:

```python
"""Pydantic schemas for Donations endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import UTCDatetime


class DonationCreate(BaseModel):
    """Schema for creating a donation record."""

    amount: int = Field(gt=0, description="Donation amount")
    nick: str | None = Field(default=None, max_length=30, description="Donor display name")
    user_id: int | None = Field(default=None, description="Donor user ID")
    date: datetime | None = Field(default=None, description="Donation date (defaults to now)")

    @field_validator("nick", mode="before")
    @classmethod
    def strip_nick(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            return v.strip()
        return v


class DonationResponse(BaseModel):
    """Schema for a single donation in API responses."""

    date: UTCDatetime
    amount: int
    nick: str | None
    user_id: int | None

    model_config = {"from_attributes": True}


class DonationListResponse(BaseModel):
    """Schema for recent donations list."""

    donations: list[DonationResponse]


class MonthlyDonationTotal(BaseModel):
    """A single month's donation total."""

    year: int
    month: int
    total: int


class MonthlyDonationResponse(BaseModel):
    """Schema for monthly donation totals."""

    monthly_totals: list[MonthlyDonationTotal]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_donation_schemas.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add app/schemas/donations.py tests/unit/test_donation_schemas.py
git commit -m "feat: add donation schemas with validation"
```

---

### Task 3: Create donations router with GET /donations endpoint

**Files:**
- Create: `app/api/v1/donations.py`
- Modify: `app/api/v1/__init__.py` — register router
- Create: `tests/api/v1/test_donations.py`

**Step 1: Write failing tests for GET /donations**

Create `tests/api/v1/test_donations.py`:

```python
"""Tests for donations API endpoints."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Donations


@pytest.fixture
async def sample_donations(db_session: AsyncSession) -> list[Donations]:
    """Create sample donation records."""
    now = datetime.now()
    donations = [
        Donations(date=now - timedelta(days=i), amount=(i + 1) * 10, nick=f"Donor {i}")
        for i in range(5)
    ]
    for d in donations:
        db_session.add(d)
    await db_session.commit()
    for d in donations:
        await db_session.refresh(d)
    return donations


class TestListDonations:
    """GET /api/v1/donations"""

    async def test_list_empty(self, client: AsyncClient):
        """Returns empty list when no donations exist."""
        response = await client.get("/api/v1/donations")
        assert response.status_code == 200
        data = response.json()
        assert data["donations"] == []

    async def test_list_returns_donations(
        self, client: AsyncClient, sample_donations: list[Donations]
    ):
        """Returns donations ordered by date descending."""
        response = await client.get("/api/v1/donations")
        assert response.status_code == 200
        data = response.json()
        assert len(data["donations"]) == 5
        # Most recent first (lowest timedelta = most recent)
        assert data["donations"][0]["amount"] == 10  # i=0, newest
        assert data["donations"][4]["amount"] == 50  # i=4, oldest

    async def test_list_respects_limit(
        self, client: AsyncClient, sample_donations: list[Donations]
    ):
        """Limit param caps the number of returned donations."""
        response = await client.get("/api/v1/donations?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["donations"]) == 2

    async def test_list_default_limit(self, client: AsyncClient, db_session: AsyncSession):
        """Default limit is 10."""
        for i in range(15):
            db_session.add(Donations(amount=10, nick=f"Donor {i}"))
        await db_session.commit()

        response = await client.get("/api/v1/donations")
        data = response.json()
        assert len(data["donations"]) == 10

    async def test_list_limit_max_50(self, client: AsyncClient):
        """Limit above 50 is rejected."""
        response = await client.get("/api/v1/donations?limit=51")
        assert response.status_code == 422

    async def test_list_limit_min_1(self, client: AsyncClient):
        """Limit below 1 is rejected."""
        response = await client.get("/api/v1/donations?limit=0")
        assert response.status_code == 422

    async def test_response_shape(
        self, client: AsyncClient, sample_donations: list[Donations]
    ):
        """Each donation has the expected fields."""
        response = await client.get("/api/v1/donations?limit=1")
        data = response.json()
        donation = data["donations"][0]
        assert "date" in donation
        assert "amount" in donation
        assert "nick" in donation
        assert "user_id" in donation
        # id field should NOT be exposed
        assert "id" not in donation
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_donations.py::TestListDonations -v`
Expected: FAIL — 404 (no route registered yet)

**Step 3: Create the donations router with list endpoint**

Create `app/api/v1/donations.py`:

```python
"""Donations API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.misc import Donations
from app.schemas.donations import DonationListResponse, DonationResponse

router = APIRouter(prefix="/donations", tags=["donations"])


@router.get("/", response_model=DonationListResponse, include_in_schema=False)
@router.get("", response_model=DonationListResponse)
async def list_donations(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DonationListResponse:
    """List recent donations, newest first."""
    query = select(Donations).order_by(desc(Donations.date)).limit(limit)
    result = await db.execute(query)
    rows = result.scalars().all()

    return DonationListResponse(
        donations=[DonationResponse.model_validate(row) for row in rows]
    )
```

**Step 4: Register router in v1 API**

In `app/api/v1/__init__.py`, add the import:

```python
from app.api.v1 import (
    admin,
    auth,
    banners,
    comments,
    donations,
    favorites,
    ...
)
```

And add:

```python
router.include_router(donations.router)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_donations.py::TestListDonations -v`
Expected: All 7 tests PASS

**Step 6: Commit**

```bash
git add app/api/v1/donations.py app/api/v1/__init__.py tests/api/v1/test_donations.py
git commit -m "feat: add GET /donations endpoint for recent donations"
```

---

### Task 4: Add GET /donations/monthly endpoint

**Files:**
- Modify: `app/api/v1/donations.py`
- Modify: `tests/api/v1/test_donations.py`

**Step 1: Write failing tests for monthly endpoint**

Add to `tests/api/v1/test_donations.py`:

```python
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta  # add to imports


@pytest.fixture
async def monthly_donations(db_session: AsyncSession) -> None:
    """Create donations across several months."""
    now = datetime.now()
    donations = [
        # Current month: 2 donations totaling 30
        Donations(date=now, amount=10, nick="A"),
        Donations(date=now - timedelta(days=1), amount=20, nick="B"),
        # Last month: 1 donation of 50
        Donations(date=now - relativedelta(months=1), amount=50, nick="C"),
        # 3 months ago: 1 donation of 100
        Donations(date=now - relativedelta(months=3), amount=100, nick="D"),
    ]
    for d in donations:
        db_session.add(d)
    await db_session.commit()


class TestMonthlyDonations:
    """GET /api/v1/donations/monthly"""

    async def test_monthly_empty(self, client: AsyncClient):
        """Returns empty list when no donations exist."""
        response = await client.get("/api/v1/donations/monthly")
        assert response.status_code == 200
        data = response.json()
        assert data["monthly_totals"] == []

    async def test_monthly_returns_totals(
        self, client: AsyncClient, monthly_donations: None
    ):
        """Returns monthly totals grouped correctly."""
        response = await client.get("/api/v1/donations/monthly?months=12")
        assert response.status_code == 200
        data = response.json()
        totals = data["monthly_totals"]
        assert len(totals) == 3  # 3 months with donations

        # Most recent month first
        assert totals[0]["total"] == 30  # current month
        assert totals[1]["total"] == 50  # last month
        assert totals[2]["total"] == 100  # 3 months ago

    async def test_monthly_respects_months_param(
        self, client: AsyncClient, monthly_donations: None
    ):
        """Months param limits how far back to look."""
        response = await client.get("/api/v1/donations/monthly?months=2")
        data = response.json()
        totals = data["monthly_totals"]
        # Should only include current month and last month, not 3 months ago
        assert len(totals) == 2

    async def test_monthly_default_6_months(
        self, client: AsyncClient, monthly_donations: None
    ):
        """Default months is 6."""
        response = await client.get("/api/v1/donations/monthly")
        data = response.json()
        # All 3 months are within the last 6 months
        assert len(data["monthly_totals"]) == 3

    async def test_monthly_max_24(self, client: AsyncClient):
        """Months above 24 is rejected."""
        response = await client.get("/api/v1/donations/monthly?months=25")
        assert response.status_code == 422

    async def test_monthly_min_1(self, client: AsyncClient):
        """Months below 1 is rejected."""
        response = await client.get("/api/v1/donations/monthly?months=0")
        assert response.status_code == 422

    async def test_monthly_response_shape(
        self, client: AsyncClient, monthly_donations: None
    ):
        """Each entry has year, month, total fields."""
        response = await client.get("/api/v1/donations/monthly")
        data = response.json()
        entry = data["monthly_totals"][0]
        assert "year" in entry
        assert "month" in entry
        assert "total" in entry
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_donations.py::TestMonthlyDonations -v`
Expected: FAIL — 404 or similar (route doesn't exist yet)

**Step 3: Implement the monthly endpoint**

Add to `app/api/v1/donations.py`:

```python
from datetime import datetime, UTC
from sqlalchemy import func, extract

# Add the monthly endpoint (must be before any /{param} routes):

@router.get("/monthly", response_model=MonthlyDonationResponse)
async def monthly_donations(
    db: Annotated[AsyncSession, Depends(get_db)],
    months: Annotated[int, Query(ge=1, le=24)] = 6,
) -> MonthlyDonationResponse:
    """Get donation totals grouped by month."""
    cutoff = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff - relativedelta(months=months - 1)

    query = (
        select(
            extract("year", Donations.date).label("year"),
            extract("month", Donations.date).label("month"),
            func.sum(Donations.amount).label("total"),
        )
        .where(Donations.date >= cutoff)
        .group_by(
            extract("year", Donations.date),
            extract("month", Donations.date),
        )
        .order_by(
            extract("year", Donations.date).desc(),
            extract("month", Donations.date).desc(),
        )
    )
    result = await db.execute(query)
    rows = result.all()

    return MonthlyDonationResponse(
        monthly_totals=[
            MonthlyDonationTotal(year=int(row.year), month=int(row.month), total=int(row.total))
            for row in rows
        ]
    )
```

Update imports at top of `donations.py`:

```python
from datetime import UTC, datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy import desc, extract, func, select
from app.schemas.donations import (
    DonationListResponse,
    DonationResponse,
    MonthlyDonationResponse,
    MonthlyDonationTotal,
)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_donations.py::TestMonthlyDonations -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/donations.py tests/api/v1/test_donations.py
git commit -m "feat: add GET /donations/monthly endpoint for monthly totals"
```

---

### Task 5: Add POST /donations endpoint (admin create)

**Files:**
- Modify: `app/api/v1/donations.py`
- Modify: `tests/api/v1/test_donations.py`

**Step 1: Write failing tests for create endpoint**

Add to `tests/api/v1/test_donations.py`:

```python
from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


async def _user_with_permission(
    db_session: AsyncSession, permission: Permission
) -> tuple[Users, str]:
    """Create user_id=2 with a given permission and return (user, token)."""
    user = await db_session.get(Users, 2)
    user.active = 1
    perm = Perms(title=permission.value, desc=permission.description)
    db_session.add(perm)
    await db_session.flush()
    user_perm = UserPerms(user_id=user.user_id, perm_id=perm.perm_id, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()
    token = create_access_token(user.user_id)
    return user, token


@pytest.fixture
async def user_with_donations_create(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with DONATIONS_CREATE permission and return (user, token)."""
    return await _user_with_permission(db_session, Permission.DONATIONS_CREATE)


@pytest.fixture
async def unprivileged_token(db_session: AsyncSession) -> str:
    """Token for an authenticated user with no donations permissions."""
    user = await db_session.get(Users, 3)
    user.active = 1
    await db_session.commit()
    return create_access_token(user.user_id)


class TestCreateDonation:
    """POST /api/v1/donations"""

    async def test_create_requires_auth(self, client: AsyncClient):
        """Returns 401 without authentication."""
        response = await client.post(
            "/api/v1/donations", json={"amount": 10}
        )
        assert response.status_code == 401

    async def test_create_requires_permission(
        self, client: AsyncClient, unprivileged_token: str
    ):
        """Returns 403 without DONATIONS_CREATE permission."""
        response = await client.post(
            "/api/v1/donations",
            json={"amount": 10},
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_create_minimal(
        self, client: AsyncClient, user_with_donations_create: tuple[Users, str]
    ):
        """Creates donation with just amount, date defaults to now."""
        _, token = user_with_donations_create
        response = await client.post(
            "/api/v1/donations",
            json={"amount": 25},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount"] == 25
        assert data["nick"] is None
        assert data["user_id"] is None
        assert data["date"] is not None

    async def test_create_full(
        self, client: AsyncClient, user_with_donations_create: tuple[Users, str]
    ):
        """Creates donation with all fields."""
        _, token = user_with_donations_create
        response = await client.post(
            "/api/v1/donations",
            json={"amount": 50, "nick": "Generous", "user_id": 123},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount"] == 50
        assert data["nick"] == "Generous"
        assert data["user_id"] == 123

    async def test_create_validates_amount_required(
        self, client: AsyncClient, user_with_donations_create: tuple[Users, str]
    ):
        """Returns 422 when amount is missing."""
        _, token = user_with_donations_create
        response = await client.post(
            "/api/v1/donations",
            json={"nick": "No Amount"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_create_validates_amount_positive(
        self, client: AsyncClient, user_with_donations_create: tuple[Users, str]
    ):
        """Returns 422 when amount is zero or negative."""
        _, token = user_with_donations_create
        response = await client.post(
            "/api/v1/donations",
            json={"amount": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_create_appears_in_list(
        self, client: AsyncClient, user_with_donations_create: tuple[Users, str]
    ):
        """Created donation appears in the list endpoint."""
        _, token = user_with_donations_create
        await client.post(
            "/api/v1/donations",
            json={"amount": 99, "nick": "Visible"},
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await client.get("/api/v1/donations")
        data = response.json()
        assert len(data["donations"]) == 1
        assert data["donations"][0]["amount"] == 99
        assert data["donations"][0]["nick"] == "Visible"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_donations.py::TestCreateDonation -v`
Expected: FAIL — 405 Method Not Allowed (POST route doesn't exist)

**Step 3: Implement the create endpoint**

Add to `app/api/v1/donations.py`:

```python
import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.auth import CurrentUser
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.schemas.donations import DonationCreate


@router.post(
    "/",
    response_model=DonationResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post("", response_model=DonationResponse, status_code=status.HTTP_201_CREATED)
async def create_donation(
    body: DonationCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> DonationResponse:
    """Create a donation record. Requires DONATIONS_CREATE permission."""
    assert current_user.user_id is not None

    if not await has_permission(
        db, current_user.user_id, Permission.DONATIONS_CREATE, redis_client
    ):
        raise HTTPException(status_code=403, detail="DONATIONS_CREATE permission required")

    donation = Donations(
        amount=body.amount,
        nick=body.nick,
        user_id=body.user_id,
    )
    if body.date is not None:
        donation.date = body.date

    db.add(donation)
    await db.commit()
    await db.refresh(donation)

    return DonationResponse.model_validate(donation)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_donations.py::TestCreateDonation -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/donations.py tests/api/v1/test_donations.py
git commit -m "feat: add POST /donations endpoint for admin creation"
```

---

### Task 6: Run full test suite and verify

**Step 1: Run all donation tests**

Run: `uv run pytest tests/api/v1/test_donations.py tests/unit/test_donation_schemas.py -v`
Expected: All tests PASS

**Step 2: Run full test suite to check for regressions**

Run: `uv run pytest --tb=short`
Expected: No regressions, all existing tests still pass

**Step 3: Commit any fixes if needed**

If any tests need adjustment, fix and commit.

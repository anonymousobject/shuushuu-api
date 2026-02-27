"""Tests for donations API endpoints."""

from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

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

"""API tests for daily upload limit: uploads_remaining_today and 429 enforcement."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.image import Images
from app.models.user import Users


def _make_user(**overrides) -> Users:
    """Create a Users instance with sensible defaults."""
    defaults = dict(
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        active=1,
        email_verified=True,
        maximgperday=15,
    )
    defaults.update(overrides)
    return Users(**defaults)


def _make_image(user_id: int, suffix: str) -> Images:
    """Create an Images instance with today's date."""
    return Images(
        filename=f"daily-{suffix}",
        ext="jpg",
        original_filename=f"daily-{suffix}.jpg",
        md5_hash=f"dailyhash-{suffix}",
        filesize=1000,
        width=100,
        height=100,
        user_id=user_id,
        status=1,
        locked=0,
        date_added=datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=None),
    )


@pytest.mark.api
class TestUploadsRemainingToday:
    """Tests for uploads_remaining_today in /users/me."""

    async def test_full_remaining_when_no_uploads(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """User with no uploads today should see full remaining count."""
        user = _make_user(username="remainfull", email="remainfull@example.com")
        db_session.add(user)
        await db_session.commit()

        token = create_access_token(user.user_id)
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["uploads_remaining_today"] == 15

    async def test_decrements_after_uploads(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """uploads_remaining_today should decrease with each upload."""
        user = _make_user(
            username="remaindecr", email="remaindecr@example.com", maximgperday=10
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add 3 images today
        for i in range(3):
            db_session.add(_make_image(user.user_id, f"decr-{i}"))
        await db_session.commit()

        token = create_access_token(user.user_id)
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["uploads_remaining_today"] == 7  # 10 - 3

    async def test_floors_at_zero(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """uploads_remaining_today should never go negative."""
        user = _make_user(
            username="remainzero", email="remainzero@example.com", maximgperday=2
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add 5 images (more than limit)
        for i in range(5):
            db_session.add(_make_image(user.user_id, f"zero-{i}"))
        await db_session.commit()

        token = create_access_token(user.user_id)
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["uploads_remaining_today"] == 0

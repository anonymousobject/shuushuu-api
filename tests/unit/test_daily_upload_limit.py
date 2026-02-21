"""Tests for daily upload limit helpers and enforcement."""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.user import Users
from app.services.upload import check_upload_rate_limit, get_uploads_today


@pytest.mark.unit
class TestGetUploadsToday:
    """Tests for get_uploads_today helper."""

    async def test_returns_zero_when_no_uploads(self, db_session: AsyncSession):
        """User with no uploads today should return 0."""
        user = Users(
            username="nouploader",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="nouploader@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        count = await get_uploads_today(user.user_id, db_session)
        assert count == 0

    async def test_counts_todays_uploads(self, db_session: AsyncSession):
        """Should count images uploaded today."""
        user = Users(
            username="dayuploader",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="dayuploader@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add 3 images with today's date
        for i in range(3):
            img = Images(
                filename=f"test-{i}",
                ext="jpg",
                original_filename=f"test-{i}.jpg",
                md5_hash=f"hash{i}daily",
                filesize=1000,
                width=100,
                height=100,
                user_id=user.user_id,
                status=1,
                locked=0,
                date_added=datetime.now(),
            )
            db_session.add(img)
        await db_session.commit()

        count = await get_uploads_today(user.user_id, db_session)
        assert count == 3

    async def test_excludes_other_users_uploads(self, db_session: AsyncSession):
        """Should only count the specified user's uploads."""
        user1 = Users(
            username="dayuser1",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="dayuser1@example.com",
            active=1,
        )
        user2 = Users(
            username="dayuser2",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="dayuser2@example.com",
            active=1,
        )
        db_session.add_all([user1, user2])
        await db_session.commit()
        await db_session.refresh(user1)
        await db_session.refresh(user2)

        # user1 uploads 2, user2 uploads 1
        for i in range(2):
            db_session.add(
                Images(
                    filename=f"u1-{i}",
                    ext="jpg",
                    original_filename=f"u1-{i}.jpg",
                    md5_hash=f"u1hash{i}daily",
                    filesize=1000,
                    width=100,
                    height=100,
                    user_id=user1.user_id,
                    status=1,
                    locked=0,
                    date_added=datetime.now(),
                )
            )
        db_session.add(
            Images(
                filename="u2-0",
                ext="jpg",
                original_filename="u2-0.jpg",
                md5_hash="u2hash0daily",
                filesize=1000,
                width=100,
                height=100,
                user_id=user2.user_id,
                status=1,
                locked=0,
                date_added=datetime.now(),
            )
        )
        await db_session.commit()

        assert await get_uploads_today(user1.user_id, db_session) == 2
        assert await get_uploads_today(user2.user_id, db_session) == 1

    async def test_ignores_yesterdays_uploads(self, db_session: AsyncSession):
        """Uploads from yesterday should not be counted toward today's total."""
        user = Users(
            username="yesterdayuploader",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="yesterdayuploader@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        yesterday = datetime.now() - timedelta(days=1)
        for i in range(2):
            db_session.add(
                Images(
                    filename=f"yesterday-{i}",
                    ext="jpg",
                    original_filename=f"yesterday-{i}.jpg",
                    md5_hash=f"yesterdayhash{i}",
                    filesize=1000,
                    width=100,
                    height=100,
                    user_id=user.user_id,
                    status=1,
                    locked=0,
                    date_added=yesterday,
                )
            )
        await db_session.commit()

        count = await get_uploads_today(user.user_id, db_session)
        assert count == 0


def _make_image(user_id: int, suffix: str) -> Images:
    """Create an Images instance with today's date but far enough back to avoid rate limit."""
    # Use 1 hour ago so it's still "today" but won't trigger per-upload rate limit
    earlier_today = datetime.now() - timedelta(hours=1)
    return Images(
        filename=f"limit-{suffix}",
        ext="jpg",
        original_filename=f"limit-{suffix}.jpg",
        md5_hash=f"limithash-{suffix}",
        filesize=1000,
        width=100,
        height=100,
        user_id=user_id,
        status=1,
        locked=0,
        date_added=earlier_today,
    )


@pytest.mark.unit
class TestDailyLimitEnforcement:
    """Tests for daily limit enforcement in check_upload_rate_limit."""

    async def test_allows_upload_under_limit(self, db_session: AsyncSession):
        """Should not raise when uploads are under the daily limit."""
        user = Users(
            username="underlimit",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="underlimit@example.com",
            active=1,
            maximgperday=5,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add 2 images (under limit of 5)
        for i in range(2):
            db_session.add(_make_image(user.user_id, f"under-{i}"))
        await db_session.commit()

        # Should not raise
        await check_upload_rate_limit(user.user_id, db_session, maximgperday=5)

    async def test_blocks_upload_at_limit(self, db_session: AsyncSession):
        """Should raise 429 when daily limit is reached."""
        user = Users(
            username="atlimit",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="atlimit@example.com",
            active=1,
            maximgperday=3,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add exactly 3 images (at limit)
        for i in range(3):
            db_session.add(_make_image(user.user_id, f"at-{i}"))
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await check_upload_rate_limit(user.user_id, db_session, maximgperday=3)
        assert exc_info.value.status_code == 429
        assert "daily upload limit" in exc_info.value.detail.lower()

    async def test_blocks_upload_over_limit(self, db_session: AsyncSession):
        """Should raise 429 when over daily limit."""
        user = Users(
            username="overlimit",
            password="hashed",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="overlimit@example.com",
            active=1,
            maximgperday=2,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Add 5 images (over limit of 2)
        for i in range(5):
            db_session.add(_make_image(user.user_id, f"over-{i}"))
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await check_upload_rate_limit(user.user_id, db_session, maximgperday=2)
        assert exc_info.value.status_code == 429
        assert "daily upload limit" in exc_info.value.detail.lower()

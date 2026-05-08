"""Tests for image background jobs (arq tasks)."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.user import Users
from app.tasks.image_jobs import add_to_iqdb_job


class TestAddToIqdbJob:
    """Tests for add_to_iqdb_job arq task."""

    @pytest.fixture
    async def indexed_image(self, db_session: AsyncSession) -> Images:
        """An Images row with no iqdb_hash yet."""
        user = Users(
            username="iqdbjob",
            password="x",
            password_type="bcrypt",
            salt="saltsalt12345678",
            email="iqdbjob@example.com",
            active=1,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        image = Images(
            filename="2026-05-07-1",
            ext="jpg",
            md5_hash="abcdef0123456789",
            filesize=1000,
            width=100,
            height=100,
            user_id=user.user_id,
            status=1,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)
        return image

    @pytest.mark.asyncio
    async def test_persists_hash_on_successful_post(
        self, indexed_image: Images, db_session: AsyncSession
    ):
        """A 200 from iqdb-rs writes the response's `hash` to images.iqdb_hash."""
        # Mock iqdb-rs returning a hash.
        fake_hash = "iqdb_" + "0" * 528
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post_id": indexed_image.image_id,
            "hash": fake_hash,
            "signature": {"avglf": [0.0, 0.0, 0.0], "sig": []},
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_response

        # Redirect AsyncSessionLocal to the test session so the UPDATE is
        # visible on the same connection (avoiding cross-connection isolation
        # issues between the test DB session and AsyncSessionLocal's engine).
        # The production code does `async with AsyncSessionLocal() as session,
        # session.begin()`. Since db_session already has an active savepoint,
        # stub out begin() as a no-op async CM so the compound `with` doesn't
        # error on "transaction already begun", while still letting execute()
        # write through to the real session.
        @asynccontextmanager
        async def _noop_begin():
            yield

        @asynccontextmanager
        async def _test_session_factory():
            original_begin = db_session.begin
            db_session.begin = _noop_begin  # type: ignore[method-assign]
            try:
                yield db_session
            finally:
                db_session.begin = original_begin  # type: ignore[method-assign]

        ctx = {"job_try": 1}

        with (
            patch("app.tasks.image_jobs.FilePath.exists", return_value=True),
            patch("builtins.open", create=True),
            patch("httpx.Client", return_value=mock_client),
            patch("app.tasks.image_jobs.AsyncSessionLocal", _test_session_factory),
        ):
            result = await add_to_iqdb_job(
                ctx, indexed_image.image_id, "/fake/path/thumb.webp"
            )

        assert result == {"success": True}

        # Re-fetch the row in a fresh select to bypass any session cache.
        image_id = indexed_image.image_id
        db_session.expire_all()
        refetched = (
            await db_session.execute(
                select(Images).where(Images.image_id == image_id)
            )
        ).scalar_one()
        assert refetched.iqdb_hash == fake_hash

    @pytest.mark.asyncio
    async def test_returns_success_when_hash_persist_fails(
        self, indexed_image: Images, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ):
        """A DB UPDATE failure after a successful iqdb POST still returns success."""
        fake_hash = "iqdb_" + "0" * 528
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "post_id": indexed_image.image_id,
            "hash": fake_hash,
            "signature": {"avglf": [0.0, 0.0, 0.0], "sig": []},
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_response

        ctx = {"job_try": 1}

        with (
            caplog.at_level(logging.WARNING, logger="app.tasks.image_jobs"),
            patch("app.tasks.image_jobs.FilePath.exists", return_value=True),
            patch("builtins.open", create=True),
            patch("httpx.Client", return_value=mock_client),
            patch(
                "app.tasks.image_jobs.AsyncSessionLocal",
                side_effect=RuntimeError("simulated db outage"),
            ),
        ):
            result = await add_to_iqdb_job(
                ctx, indexed_image.image_id, "/fake/path/thumb.webp"
            )

        assert result == {"success": True}
        assert any(
            "iqdb_hash_persist_failed" in r.message and "simulated db outage" in r.message
            for r in caplog.records
        ), f"Expected iqdb_hash_persist_failed warning, got: {[r.message for r in caplog.records]}"

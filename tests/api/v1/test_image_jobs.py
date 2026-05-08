"""Tests for image background jobs (arq tasks)."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.image import Images
from app.models.user import Users
from app.tasks.image_jobs import add_to_iqdb_job


def _mock_iqdb_post(image_id: int, iqdb_hash: str):
    """Patch httpx.Client to return a successful iqdb-rs /images/{id} response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "post_id": image_id,
        "hash": iqdb_hash,
        "signature": {"avglf": [0.0, 0.0, 0.0], "sig": []},
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = mock_response
    return patch("httpx.Client", return_value=mock_client)


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
    @pytest.mark.needs_commit
    async def test_persists_hash_on_successful_post(
        self, indexed_image: Images, db_session: AsyncSession, engine: AsyncEngine
    ):
        """A 200 from iqdb-rs writes the response's `hash` to images.iqdb_hash.

        Uses @pytest.mark.needs_commit so db_session uses real commits.
        AsyncSessionLocal is patched to a real async_sessionmaker backed by the
        test engine — this exercises the production transaction path
        (AsyncSessionLocal() as session, session.begin()) without hitting the
        production DB.
        """
        fake_hash = "iqdb_" + "0" * 528
        ctx = {"job_try": 1}

        # Real session factory pointing to the test DB — exercises the
        # production code's `async with AsyncSessionLocal() as session,
        # session.begin()` path with real transaction management.
        test_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        with (
            _mock_iqdb_post(indexed_image.image_id, fake_hash),
            patch("app.tasks.image_jobs.FilePath.exists", return_value=True),
            patch("builtins.open", create=True),
            patch("app.tasks.image_jobs.AsyncSessionLocal", test_session_factory),
        ):
            result = await add_to_iqdb_job(
                ctx, indexed_image.image_id, "/fake/path/thumb.webp"
            )

        assert result == {"success": True}

        # Use a fresh session to verify the commit — db_session's open
        # REPEATABLE READ transaction would see a stale snapshot.
        image_id = indexed_image.image_id
        async with test_session_factory() as verify_session:
            refetched = (
                await verify_session.execute(
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
        ctx = {"job_try": 1}

        with (
            caplog.at_level(logging.WARNING, logger="app.tasks.image_jobs"),
            _mock_iqdb_post(indexed_image.image_id, fake_hash),
            patch("app.tasks.image_jobs.FilePath.exists", return_value=True),
            patch("builtins.open", create=True),
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

        # Spec contract: failed UPDATE leaves iqdb_hash NULL so the next
        # reindex retries.
        refetched = (
            await db_session.execute(
                select(Images).where(Images.image_id == indexed_image.image_id)
            )
        ).scalar_one()
        assert refetched.iqdb_hash is None

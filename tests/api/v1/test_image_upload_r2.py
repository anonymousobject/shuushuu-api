"""Tests for R2 finalize job enqueued after upload."""

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, get_password_hash
from app.models.user import Users


def _fake_image_bytes() -> bytes:
    """Create a minimal valid JPEG for upload tests."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def uploader(db_session: AsyncSession) -> Users:
    user = Users(
        username="r2uploader",
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="r2uploader@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.api
class TestUploadEnqueuesR2Finalize:
    async def test_enqueues_finalize_when_r2_enabled(
        self, client: AsyncClient, uploader: Users, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", True)
        token = create_access_token(uploader.user_id)

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            response = await client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code in (200, 201)
        finalize_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_finalize_upload_job"
        ]
        assert len(finalize_calls) == 1
        assert finalize_calls[0].kwargs.get("_defer_by", 0) >= 60

    async def test_no_finalize_when_r2_disabled(
        self, client: AsyncClient, uploader: Users, monkeypatch
    ):
        monkeypatch.setattr(settings, "R2_ENABLED", False)
        token = create_access_token(uploader.user_id)

        with patch(
            "app.api.v1.images.enqueue_job", new_callable=AsyncMock
        ) as mock_enqueue:
            await client.post(
                "/api/v1/images/upload",
                files={"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")},
                headers={"Authorization": f"Bearer {token}"},
            )
        finalize_calls = [
            c
            for c in mock_enqueue.await_args_list
            if c.args[0] == "r2_finalize_upload_job"
        ]
        assert finalize_calls == []

"""Tests for the stateless upload-form tag analysis endpoint."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType, settings
from app.core.security import create_access_token
from app.models.tag import Tags
from app.models.user import Users
from app.services import ml_runtime


@pytest.fixture
async def verified_user(db_session: AsyncSession) -> Users:
    """Create a verified user for analyze testing."""
    user = Users(
        username="analyzer",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="analyzer@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def analyze_client(client: AsyncClient, verified_user: Users) -> AsyncClient:
    """Authenticated client with a verified user."""
    access_token = create_access_token(verified_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


def _fake_image_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid JPEG for analyze tests."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _fake_ml_service(raw_preds: list[dict]):
    """Build a fake ML service whose generate_raw_predictions returns canned raw dicts."""

    class _FakeService:
        async def generate_raw_predictions(self, image_path, *, include_categories, min_confidence):
            return raw_preds

    return _FakeService()


def _files():
    return {"file": ("test.jpg", _fake_image_bytes(), "image/jpeg")}


async def test_analyze_503_when_disabled(analyze_client: AsyncClient, monkeypatch):
    """When the feature flag is off the endpoint returns 503."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", False)

    response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 503, response.text


async def test_analyze_requires_auth(client: AsyncClient, monkeypatch):
    """Without an auth header the endpoint returns 401."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    response = await client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 401, response.text


async def test_analyze_returns_theme_and_character_tags(
    analyze_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Happy path: a theme and a character tag are resolved and returned with correct types."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    theme_tag = Tags(title="smile", type=TagType.THEME)  # type=1
    character_tag = Tags(title="hatsune miku", type=TagType.CHARACTER)  # type=4
    db_session.add(theme_tag)
    db_session.add(character_tag)
    await db_session.commit()
    await db_session.refresh(theme_tag)
    await db_session.refresh(character_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
        {"external_tag": "hatsune_miku", "confidence": 0.95, "category": 4, "model_version": "v1"},
    ]
    resolved = [
        {"tag_id": theme_tag.tag_id, "confidence": 0.9, "model_version": "v1"},
        {"tag_id": character_tag.tag_id, "confidence": 0.95, "model_version": "v1"},
    ]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    data = response.json()
    titles = {s["title"]: s["type"] for s in data["suggestions"]}
    assert titles.get("smile") == TagType.THEME
    assert titles.get("hatsune miku") == TagType.CHARACTER


async def test_analyze_rejects_oversize_dimensions(analyze_client: AsyncClient, monkeypatch):
    """An image whose longest edge exceeds the cap returns 400 before inference."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "ML_ANALYZE_MAX_DIMENSION", 10)

    response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 400, response.text


async def test_analyze_busy_returns_429(analyze_client: AsyncClient, monkeypatch):
    """When no inference slot frees up within the timeout the endpoint returns 429."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
    ]

    # Fill a 1-slot semaphore so the request must wait, with a tiny timeout.
    full_semaphore = asyncio.Semaphore(1)
    await full_semaphore.acquire()
    monkeypatch.setattr(ml_runtime, "_inference_semaphore", full_semaphore)
    monkeypatch.setattr(ml_runtime.settings, "ML_ANALYZE_SEMAPHORE_TIMEOUT", 0.05)

    with patch(
        "app.api.v1.ml_analyze.get_ml_service",
        new_callable=AsyncMock,
        return_value=_fake_ml_service(raw_preds),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 429, response.text


async def test_analyze_caches_predictions(
    analyze_client: AsyncClient, db_session: AsyncSession, mock_redis, monkeypatch
):
    """Successful analyze writes the raw predictions to redis under an ml:analyze: key."""
    monkeypatch.setattr(settings, "ML_TAG_SUGGESTIONS_ENABLED", True)

    theme_tag = Tags(title="smile", type=TagType.THEME)
    db_session.add(theme_tag)
    await db_session.commit()
    await db_session.refresh(theme_tag)

    raw_preds = [
        {"external_tag": "smile", "confidence": 0.9, "category": 0, "model_version": "v1"},
    ]
    resolved = [{"tag_id": theme_tag.tag_id, "confidence": 0.9, "model_version": "v1"}]

    with (
        patch(
            "app.api.v1.ml_analyze.get_ml_service",
            new_callable=AsyncMock,
            return_value=_fake_ml_service(raw_preds),
        ),
        patch(
            "app.api.v1.ml_analyze.resolve_external_tags",
            new_callable=AsyncMock,
            return_value=resolved,
        ),
    ):
        response = await analyze_client.post("/api/v1/ml-tag-suggestions/analyze", files=_files())

    assert response.status_code == 200, response.text
    assert mock_redis.set.await_count >= 1
    cache_keys = [call.args[0] for call in mock_redis.set.await_args_list]
    assert any(key.startswith("ml:analyze:") for key in cache_keys)

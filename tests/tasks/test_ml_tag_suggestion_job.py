"""
Tests for the ML tag suggestion arq job wrapper.

The job is a thin wrapper: it opens a session, loads the image, checks for a
loaded ml_service in ctx, and delegates to the shared pipeline. These tests
verify the wrapper's own behavior (missing image, missing service, error
translation, happy path). The generation logic itself is covered in
tests/services/test_ml_suggestion_pipeline.py.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.config import settings
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.user import Users
from app.tasks.ml_tag_suggestion_job import generate_ml_tag_suggestions

JOB = "app.tasks.ml_tag_suggestion_job"
PIPELINE = "app.services.ml_suggestion_pipeline"


class FakeMLService:
    """Canned ML service: returns raw predictions for the pipeline."""

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions

    async def generate_raw_predictions(
        self,
        image_path: str,
        *,
        include_categories: set[int],
        min_confidence: float,
    ) -> list[dict[str, Any]]:
        return list(self._predictions)


def _session_cm(db_session):
    """async-context-manager double that yields the test's db_session.

    The job opens `async with get_async_session() as db:`, which would bypass
    the test's SAVEPOINT isolation. Routing it back to db_session keeps the
    job's writes inside the test transaction.
    """
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


async def _make_user(db_session, suffix: str) -> Users:
    user = Users(
        username=f"job_{suffix}",
        email=f"job_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_missing_image_returns_error(db_session):
    """An image_id with no row returns an error dict, doesn't raise."""
    with patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)):
        result = await generate_ml_tag_suggestions({"ml_service": object()}, image_id=999999)

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


async def test_missing_ml_service_returns_error(db_session, tmp_path, monkeypatch):
    """No ml_service in ctx returns an error dict mentioning the feature flag."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    user = await _make_user(db_session, "noservice")
    image = Images(
        filename="2024-01-01-noservice",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_noservice",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    with patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)):
        result = await generate_ml_tag_suggestions({}, image_id=image.image_id)

    assert result["status"] == "error"
    assert "ML_TAG_SUGGESTIONS_ENABLED" in result["error"]


async def test_happy_path_creates_rows(db_session, tmp_path, monkeypatch):
    """With a fake service and a real file, the job stores pending suggestions."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))

    tags = [Tags(tag_id=46, title="long hair"), Tags(tag_id=25, title="blush")]
    db_session.add_all(tags)
    await db_session.flush()

    user = await _make_user(db_session, "happy")
    image = Images(
        filename="2024-01-01-happy",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_happy",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.flush()
    fake_image = tmp_path / "fullsize" / f"{image.filename}.{image.ext}"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")
    await db_session.commit()

    ml = FakeMLService(
        [
            {"external_tag": "long_hair", "confidence": 0.9, "category": 0, "model_version": "v3"},
            {"external_tag": "blush", "confidence": 0.9, "category": 0, "model_version": "v3"},
        ]
    )
    mapped = [
        {"tag_id": 46, "confidence": 0.92, "model_version": "v3"},
        {"tag_id": 25, "confidence": 0.85, "model_version": "v3"},
    ]

    async def _resolver(db, suggestions):
        return [dict(r) for r in mapped]

    async def _passthrough(db, suggestions):
        return suggestions

    with (
        patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)),
        patch(f"{PIPELINE}.resolve_external_tags", _resolver),
        patch(f"{PIPELINE}.resolve_tag_relationships", _passthrough),
    ):
        result = await generate_ml_tag_suggestions({"ml_service": ml}, image_id=image.image_id)

    assert result["status"] == "completed"
    assert result["suggestions_created"] == 2

    db_result = await db_session.execute(
        select(MlTagSuggestions).where(MlTagSuggestions.image_id == image.image_id)
    )
    suggestions = db_result.scalars().all()
    assert {s.tag_id for s in suggestions} == {46, 25}
    assert all(s.status == "pending" for s in suggestions)


async def test_missing_file_translates_to_error(db_session, tmp_path, monkeypatch):
    """FileNotFoundError from the pipeline becomes an error dict (no crash)."""
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    user = await _make_user(db_session, "nofile")
    image = Images(
        filename="2024-01-01-nofile",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="hash_nofile",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    ml = FakeMLService([])
    with patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)):
        result = await generate_ml_tag_suggestions({"ml_service": ml}, image_id=image.image_id)

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


async def test_job_reuses_cached_predictions_without_inference(db_session, monkeypatch):
    """Cache HIT: persist_predictions is called with cached raw; no inference occurs."""
    user = await _make_user(db_session, "cached")
    image = Images(
        filename="2024-01-01-cached",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="abc123",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    import json

    raw = [{"external_tag": "long_hair", "confidence": 0.9, "category": 0, "model_version": "v3"}]

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=json.dumps(raw))
    fake_redis.aclose = AsyncMock(return_value=None)

    persisted = {}

    async def fake_persist(db, image_id, preds):
        persisted["preds"] = preds
        return len(preds)

    sentinel_service = object()  # must NOT be used for inference on a cache hit
    with (
        patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)),
        patch(f"{JOB}._analyze_redis", return_value=fake_redis),
        patch(f"{JOB}.persist_predictions", fake_persist),
    ):
        result = await generate_ml_tag_suggestions(
            {"ml_service": sentinel_service}, image_id=image.image_id
        )

    assert result["status"] == "completed"
    assert persisted["preds"] == raw


async def test_job_falls_back_to_inference_on_cache_miss(db_session, monkeypatch):
    """Cache MISS: generate_and_store_suggestions is called (inference path)."""
    user = await _make_user(db_session, "miss")
    image = Images(
        filename="2024-01-01-miss",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="misshash",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.aclose = AsyncMock(return_value=None)

    called = {}

    async def fake_generate(db, img, ml_service):
        called["yes"] = True
        return 0

    with (
        patch(f"{JOB}.get_async_session", return_value=_session_cm(db_session)),
        patch(f"{JOB}._analyze_redis", return_value=fake_redis),
        patch(f"{JOB}.generate_and_store_suggestions", fake_generate),
    ):
        result = await generate_ml_tag_suggestions(
            {"ml_service": object()}, image_id=image.image_id
        )

    assert called.get("yes") is True
    assert result["status"] == "completed"

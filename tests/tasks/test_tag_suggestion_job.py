"""
Tests for tag suggestion generation background job.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.tag_suggestion import TagSuggestion
from app.models.user import Users
from app.tasks.tag_suggestion_job import generate_tag_suggestions


@asynccontextmanager
async def mock_get_async_session(db_session):
    """Mock get_async_session to return the test database session"""
    yield db_session


@pytest.mark.asyncio
async def test_generate_tag_suggestions_creates_suggestions(db_session, tmp_path):
    """Test that job generates tag suggestions and stores them in database."""
    # Create test tags first
    tag1 = Tags(tag_id=46, tag="long_hair")
    tag2 = Tags(tag_id=161, tag="short_hair")
    tag3 = Tags(tag_id=25, tag="blush")
    db_session.add_all([tag1, tag2, tag3])
    await db_session.flush()

    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_1",
        email=f"test{id(db_session)}_1@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-1",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"abc123def456_{id(db_session)}_1",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock ML service predictions (now returns external_tag format)
    mock_predictions = [
        {"external_tag": "long_hair", "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "short_hair", "confidence": 0.88, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "blush", "confidence": 0.85, "model_source": "danbooru", "model_version": "v3"},
    ]

    # Mock ML service
    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

    # Mock tag mapping resolver (converts external_tag to tag_id)
    async def mock_mapping_resolver(db, suggestions):
        return [
            {"tag_id": 46, "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 25, "confidence": 0.85, "model_source": "danbooru", "model_version": "v3"},
        ]

    # Mock tag resolver (just pass through for this test)
    async def mock_tag_resolver(db, suggestions):
        return suggestions

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-1.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_mapping_resolver),
        patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_tag_resolver),
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        # Run the job
        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Verify result
    assert result["status"] == "completed"
    assert result["suggestions_created"] == 3

    # Verify suggestions were created in database
    query = select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
    db_result = await db_session.execute(query)
    suggestions = db_result.scalars().all()

    assert len(suggestions) == 3
    assert all(s.status == "pending" for s in suggestions)
    assert all(s.confidence >= 0.6 for s in suggestions)

    # Verify tag IDs match
    tag_ids = {s.tag_id for s in suggestions}
    assert tag_ids == {46, 161, 25}


@pytest.mark.asyncio
async def test_generate_tag_suggestions_skips_existing_tags(db_session, tmp_path):
    """Test that job skips tags already applied to the image."""
    # Create test tags
    tag1 = Tags(tag_id=46, tag="long_hair")
    tag2 = Tags(tag_id=161, tag="short_hair")
    tag3 = Tags(tag_id=25, tag="blush")
    db_session.add_all([tag1, tag2, tag3])
    await db_session.flush()

    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_2",
        email=f"test{id(db_session)}_2@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-2",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"xyz789_{id(db_session)}_2",
        filesize=2048,
        width=1024,
        height=768,
    )
    db_session.add(image)
    await db_session.flush()

    # Create existing TagLink for tag 46
    existing_link = TagLinks(image_id=image.image_id, tag_id=46, user_id=user.user_id)
    db_session.add(existing_link)
    await db_session.commit()

    # Mock ML service predictions
    mock_predictions = [
        {"external_tag": "long_hair", "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "short_hair", "confidence": 0.88, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "blush", "confidence": 0.85, "model_source": "danbooru", "model_version": "v3"},
    ]

    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

    # Mock resolvers (tag 46 is already linked but will still be returned by resolvers)
    async def mock_mapping_resolver(db, suggestions):
        return [
            {"tag_id": 46, "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 161, "confidence": 0.88, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 25, "confidence": 0.85, "model_source": "danbooru", "model_version": "v3"},
        ]

    async def mock_tag_resolver(db, suggestions):
        return suggestions

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-2.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_mapping_resolver),
        patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_tag_resolver),
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Verify result - should only create 2 suggestions (skip tag 46)
    assert result["status"] == "completed"
    assert result["suggestions_created"] == 2

    # Verify only 2 suggestions created (tag 46 was skipped)
    query = select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
    db_result = await db_session.execute(query)
    suggestions = db_result.scalars().all()

    assert len(suggestions) == 2
    tag_ids = {s.tag_id for s in suggestions}
    assert tag_ids == {161, 25}  # Tag 46 was skipped


@pytest.mark.asyncio
async def test_generate_tag_suggestions_resolves_tag_relationships(db_session, tmp_path):
    """Test that job uses tag resolver to handle aliases and hierarchies."""
    # Create test tags (alias source and canonical)
    tag_alias = Tags(tag_id=100, tag="tag_alias")
    tag_canonical = Tags(tag_id=101, tag="tag_canonical")
    db_session.add_all([tag_alias, tag_canonical])
    await db_session.flush()

    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_3",
        email=f"test{id(db_session)}_3@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-3",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"resolve123_{id(db_session)}_3",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock ML predictions
    mock_predictions = [
        {"external_tag": "some_tag", "confidence": 0.90, "model_source": "danbooru", "model_version": "v3"},
    ]

    # Mock tag mapping resolver
    async def mock_mapping_resolver(db, suggestions):
        return [{"tag_id": 100, "confidence": 0.90, "model_source": "danbooru", "model_version": "v3"}]

    # Mock tag resolver - simulates alias resolution
    async def mock_tag_resolver(db, suggestions):
        # Simulate resolving tag 100 to tag 101 (alias)
        resolved = []
        for sugg in suggestions:
            if sugg["tag_id"] == 100:
                # Replace with canonical tag
                resolved.append({
                    **sugg,
                    "tag_id": 101,
                    "resolved_from_alias": True,
                })
        return resolved

    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-3.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_mapping_resolver),
        patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_tag_resolver),
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Verify suggestion was created with resolved tag ID
    query = select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
    db_result = await db_session.execute(query)
    suggestions = db_result.scalars().all()

    assert len(suggestions) == 1
    assert suggestions[0].tag_id == 101  # Resolved from 100 to 101


@pytest.mark.asyncio
async def test_generate_tag_suggestions_handles_missing_image_file(db_session, tmp_path):
    """Test that job handles missing image files gracefully."""
    # Create test user and image record (but don't create the actual file)
    user = Users(
        username=f"user_miss",
        email=f"test_miss@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-999",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"missing123_{id(db_session)}",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock ML service (won't be called since file doesn't exist)
    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock()

    with (
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}

        # Try to generate suggestions for image with missing file
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Should return error status
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()

    # ML service should not have been called
    mock_ml_service.generate_suggestions.assert_not_called()


@pytest.mark.asyncio
async def test_generate_tag_suggestions_handles_ml_service_error(db_session, tmp_path):
    """Test that job handles ML service errors gracefully."""
    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_4",
        email=f"test{id(db_session)}_4@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-4",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"error123_{id(db_session)}_4",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock ML service that raises an error
    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(
        side_effect=RuntimeError("Model inference failed")
    )

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-4.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Job should handle error gracefully
    assert result["status"] == "error"
    assert "Model inference failed" in result["error"]

    # No suggestions should be created
    query = select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
    db_result = await db_session.execute(query)
    suggestions = db_result.scalars().all()
    assert len(suggestions) == 0


@pytest.mark.asyncio
async def test_generate_tag_suggestions_filters_low_confidence(db_session, tmp_path):
    """Test that job only creates suggestions above confidence threshold."""
    # Create test tags
    tag1 = Tags(tag_id=46, tag="long_hair")
    tag2 = Tags(tag_id=161, tag="short_hair")
    tag3 = Tags(tag_id=25, tag="blush")
    db_session.add_all([tag1, tag2, tag3])
    await db_session.flush()

    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_5",
        email=f"test{id(db_session)}_5@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-5",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"conf123_{id(db_session)}_5",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock predictions
    mock_predictions = [
        {"external_tag": "long_hair", "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "short_hair", "confidence": 0.55, "model_source": "danbooru", "model_version": "v3"},
        {"external_tag": "blush", "confidence": 0.65, "model_source": "danbooru", "model_version": "v3"},
    ]

    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

    # Mock resolvers - return varying confidence values
    async def mock_mapping_resolver(db, suggestions):
        return [
            {"tag_id": 46, "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 161, "confidence": 0.55, "model_source": "danbooru", "model_version": "v3"},
            {"tag_id": 25, "confidence": 0.65, "model_source": "danbooru", "model_version": "v3"},
        ]

    async def mock_tag_resolver(db, suggestions):
        return suggestions

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-5.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_mapping_resolver),
        patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_tag_resolver),
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Verify only high-confidence suggestions were created
    query = select(TagSuggestion).where(TagSuggestion.image_id == image.image_id)
    db_result = await db_session.execute(query)
    suggestions = db_result.scalars().all()

    # Should have 2 suggestions (filtered out confidence < 0.6)
    assert len(suggestions) == 2
    tag_ids = {s.tag_id for s in suggestions}
    assert tag_ids == {46, 25}
    assert all(s.confidence >= 0.6 for s in suggestions)


@pytest.mark.asyncio
async def test_generate_tag_suggestions_handles_no_mappings(db_session, tmp_path):
    """Test that job handles case where no tags can be mapped."""
    # Create test user and image
    user = Users(
        username=f"testuser_{id(db_session)}_6",
        email=f"test{id(db_session)}_6@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="2024-01-01-6",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"nomap123_{id(db_session)}_6",
        filesize=1024,
        width=800,
        height=600,
    )
    db_session.add(image)
    await db_session.commit()

    # Mock predictions
    mock_predictions = [
        {"external_tag": "unmapped_tag", "confidence": 0.92, "model_source": "danbooru", "model_version": "v3"},
    ]

    mock_ml_service = MagicMock()
    mock_ml_service.generate_suggestions = AsyncMock(return_value=mock_predictions)

    # Mock resolvers - no mappings found
    async def mock_mapping_resolver(db, suggestions):
        return []  # No mappings

    async def mock_tag_resolver(db, suggestions):
        return suggestions

    # Create fake image file
    fake_image = tmp_path / "fullsize" / "2024-01-01-6.jpg"
    fake_image.parent.mkdir(parents=True, exist_ok=True)
    fake_image.write_bytes(b"fake image data")

    with (
        patch("app.tasks.tag_suggestion_job.resolve_external_tags", mock_mapping_resolver),
        patch("app.tasks.tag_suggestion_job.resolve_tag_relationships", mock_tag_resolver),
        patch("app.tasks.tag_suggestion_job.settings") as mock_settings,
        patch("app.tasks.tag_suggestion_job.get_async_session", lambda: mock_get_async_session(db_session)),
    ):
        mock_settings.STORAGE_PATH = str(tmp_path)

        ctx = {"ml_service": mock_ml_service}
        result = await generate_tag_suggestions(ctx, image.image_id)

    # Should complete but create 0 suggestions
    assert result["status"] == "completed"
    assert result["suggestions_created"] == 0

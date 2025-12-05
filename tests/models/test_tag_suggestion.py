# tests/models/test_tag_suggestion.py

import pytest
from datetime import datetime
from app.models.tag_suggestion import TagSuggestion
from app.models.image import Images
from app.models.tag import Tags
from app.models.user import Users


@pytest.mark.asyncio
async def test_tag_suggestion_model_creation(db_session):
    """Test creating a TagSuggestion instance"""
    # Create dependencies
    user = Users(
        username="tag_suggestion_test_user",
        email="tag_suggestion@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="test",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="abc123",
        filesize=1024,
        width=800,
        height=600
    )
    db_session.add(image)
    await db_session.flush()

    tag = Tags(title="long hair", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    # Create suggestion
    suggestion = TagSuggestion(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.92,
        model_source="custom_theme",
        model_version="v1",
        status="pending"
    )
    db_session.add(suggestion)
    await db_session.commit()
    await db_session.refresh(suggestion)

    # Verify
    assert suggestion.suggestion_id is not None
    assert suggestion.confidence == 0.92
    assert suggestion.status == "pending"
    assert suggestion.created_at is not None


@pytest.mark.asyncio
async def test_tag_suggestion_unique_constraint(db_session):
    """Test that same tag cannot be suggested twice for same image"""
    user = Users(
        username="tag_suggestion_test_user2",
        email="tag_suggestion2@example.com",
        password="hashed",
        salt="testsalt12345678"
    )
    db_session.add(user)
    await db_session.flush()

    image = Images(
        filename="test",
        ext="jpg",
        user_id=user.user_id,
        md5_hash="abc123",
        filesize=1024,
        width=800,
        height=600
    )
    db_session.add(image)
    await db_session.flush()

    tag = Tags(title="long hair", type=1, user_id=user.user_id)
    db_session.add(tag)
    await db_session.flush()

    # First suggestion
    suggestion1 = TagSuggestion(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.9,
        model_source="custom_theme",
        model_version="v1",
        status="pending"
    )
    db_session.add(suggestion1)
    await db_session.commit()

    # Second suggestion (duplicate)
    suggestion2 = TagSuggestion(
        image_id=image.image_id,
        tag_id=tag.tag_id,
        confidence=0.95,
        model_source="danbooru",
        model_version="wd14_v2",
        status="pending"
    )
    db_session.add(suggestion2)

    # Should raise IntegrityError due to UNIQUE constraint
    with pytest.raises(Exception):  # IntegrityError or similar
        await db_session.commit()

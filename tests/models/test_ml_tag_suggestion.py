"""Tests for the MlTagSuggestions model against a real database."""

import pytest

from app.models.ml_tag_suggestion import MlTagSuggestions


async def test_ml_tag_suggestion_model_creation(db_session, test_image, test_tag):
    """Creating and persisting a suggestion populates server-side defaults."""
    suggestion = MlTagSuggestions(
        image_id=test_image.image_id,
        tag_id=test_tag.tag_id,
        confidence=0.92,
        model_version="wd-swinv2-tagger-v3",
        status="pending",
    )
    db_session.add(suggestion)
    await db_session.commit()
    await db_session.refresh(suggestion)

    assert suggestion.suggestion_id is not None
    assert suggestion.confidence == 0.92
    assert suggestion.model_version == "wd-swinv2-tagger-v3"
    assert suggestion.status == "pending"
    assert suggestion.created_at is not None


async def test_ml_tag_suggestion_unique_constraint(db_session, test_image, test_tag):
    """The same tag cannot be suggested twice for the same image."""
    suggestion1 = MlTagSuggestions(
        image_id=test_image.image_id,
        tag_id=test_tag.tag_id,
        confidence=0.9,
        model_version="wd-swinv2-tagger-v3",
        status="pending",
    )
    db_session.add(suggestion1)
    await db_session.commit()

    suggestion2 = MlTagSuggestions(
        image_id=test_image.image_id,
        tag_id=test_tag.tag_id,
        confidence=0.95,
        model_version="wd-swinv2-tagger-v3",
        status="pending",
    )
    db_session.add(suggestion2)

    with pytest.raises(Exception):  # IntegrityError on (image_id, tag_id)
        await db_session.commit()

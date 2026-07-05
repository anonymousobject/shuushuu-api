"""Tests for the MlTagSuggestions model against a real database."""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.models.ml_tag_suggestion import MlTagSuggestions
from tests.conftest import TEST_DATABASE_URL_SYNC


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


@pytest.mark.needs_commit  # failed commit needs real-commit + truncate isolation
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

    with pytest.raises(IntegrityError):  # unique (image_id, tag_id)
        await db_session.commit()


def test_status_tag_index_covers_confidence():
    """idx_ml_suggestion_status_tag must cover (status, tag_id, confidence).

    list_pending_for_tag (app/services/ml_suggestion_queue.py) filters on
    status='pending' AND tag_id=? (both equality) AND confidence >= ?, then
    orders by confidence DESC. A 2-column (status, tag_id) index only serves
    the equality predicates, so MariaDB has to filesort every pending row for
    the tag before LIMIT applies -- a cost that scales with the per-tag
    backlog. Adding confidence as the third column lets the two leading
    equality predicates narrow to a tight range, which MariaDB can then scan
    on confidence (serving the >= filter) and read in reverse to satisfy
    ORDER BY confidence DESC without a sort.
    """
    engine = create_engine(TEST_DATABASE_URL_SYNC)
    try:
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("ml_tag_suggestions")}
    finally:
        engine.dispose()

    assert "idx_ml_suggestion_status_tag" in indexes
    assert indexes["idx_ml_suggestion_status_tag"]["column_names"] == [
        "status",
        "tag_id",
        "confidence",
    ]

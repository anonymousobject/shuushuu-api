# tests/schemas/test_tag_suggestion_schemas.py

from datetime import datetime
from app.schemas.tag_suggestion import (
    TagSuggestionResponse,
    TagSuggestionsListResponse,
    ReviewSuggestionRequest,
    ReviewSuggestionsRequest
)
from app.schemas.tag import TagResponse


def test_tag_suggestion_response_schema():
    """Test TagSuggestionResponse serialization"""
    tag = TagResponse(tag_id=46, title="long hair", type=1, date_added=datetime.utcnow())

    suggestion = TagSuggestionResponse(
        suggestion_id=1,
        tag=tag,
        confidence=0.92,
        model_source="custom_theme",
        status="pending",
        created_at=datetime.utcnow()
    )

    assert suggestion.suggestion_id == 1
    assert suggestion.tag.title == "long hair"
    assert suggestion.confidence == 0.92


def test_review_suggestion_request_validation():
    """Test ReviewSuggestionRequest validates action"""
    import pytest
    from pydantic import ValidationError

    # Valid
    req = ReviewSuggestionRequest(suggestion_id=1, action="approve")
    assert req.action == "approve"

    # Invalid action
    with pytest.raises(ValidationError):
        ReviewSuggestionRequest(suggestion_id=1, action="invalid")

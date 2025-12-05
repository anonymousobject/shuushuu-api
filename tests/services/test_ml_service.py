import pytest
from app.services.ml_service import MLTagSuggestionService


@pytest.mark.asyncio
async def test_ml_service_generate_suggestions():
    """Test ML service generates mock suggestions"""
    service = MLTagSuggestionService()
    await service.load_models()

    # For now, we're using mock models that return predefined suggestions
    suggestions = await service.generate_suggestions("path/to/image.jpg")

    assert isinstance(suggestions, list)
    assert len(suggestions) > 0
    assert "tag_id" in suggestions[0]
    assert "confidence" in suggestions[0]
    assert "model_source" in suggestions[0]


@pytest.mark.asyncio
async def test_ml_service_filters_by_confidence():
    """Test that low confidence suggestions are filtered"""
    service = MLTagSuggestionService()
    await service.load_models()

    suggestions = await service.generate_suggestions(
        "path/to/image.jpg",
        min_confidence=0.8
    )

    # All suggestions should be above threshold
    assert all(s["confidence"] >= 0.8 for s in suggestions)

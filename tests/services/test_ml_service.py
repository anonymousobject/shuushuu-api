"""
Tests for ML Tag Suggestion Service.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.ml_service import MLTagSuggestionService


@pytest.fixture
def sample_image(tmp_path) -> Path:
    """Create a simple test image."""
    from PIL import Image

    img_path = tmp_path / "test_image.jpg"
    # Create a simple 100x100 white image
    img = Image.new("RGB", (100, 100), color="white")
    img.save(img_path)
    return img_path


@pytest.mark.asyncio
async def test_ml_service_generate_suggestions_with_mock():
    """Test ML service generates suggestions using mock model."""
    # Force mock model by patching settings
    with patch("app.services.ml_service.settings") as mock_settings:
        mock_settings.ML_MODELS_PATH = "/nonexistent/path"

        service = MLTagSuggestionService()
        await service.load_models()

        # Uses mock model (no real model file)
        assert service.using_mock is True

        suggestions = await service.generate_suggestions("path/to/image.jpg")

        assert isinstance(suggestions, list)
        assert len(suggestions) > 0

        # Check expected keys for Danbooru format
        assert "external_tag" in suggestions[0]
        assert "confidence" in suggestions[0]
        assert "model_source" in suggestions[0]
        assert "model_version" in suggestions[0]

        # Verify model source is danbooru
        assert suggestions[0]["model_source"] == "danbooru"
        assert suggestions[0]["model_version"] == "mock"


@pytest.mark.asyncio
async def test_ml_service_filters_by_confidence_with_mock():
    """Test that low confidence suggestions are filtered."""
    with patch("app.services.ml_service.settings") as mock_settings:
        mock_settings.ML_MODELS_PATH = "/nonexistent/path"

        service = MLTagSuggestionService()
        await service.load_models()

        suggestions = await service.generate_suggestions(
            "path/to/image.jpg",
            min_confidence=0.8,
        )

        # All suggestions should be above threshold
        assert all(s["confidence"] >= 0.8 for s in suggestions)


@pytest.mark.asyncio
async def test_ml_service_uses_mock_when_model_missing():
    """Test that service falls back to mock model when ONNX file missing."""
    with patch("app.services.ml_service.settings") as mock_settings:
        mock_settings.ML_MODELS_PATH = "/nonexistent/path"

        service = MLTagSuggestionService()
        await service.load_models()

        # Without real model files, should use mock
        assert service.using_mock is True


@pytest.mark.asyncio
async def test_ml_service_cleanup():
    """Test service cleanup releases resources."""
    with patch("app.services.ml_service.settings") as mock_settings:
        mock_settings.ML_MODELS_PATH = "/nonexistent/path"

        service = MLTagSuggestionService()
        await service.load_models()

        await service.cleanup()

        assert service.danbooru_model is None


@pytest.mark.asyncio
async def test_ml_service_with_real_model(sample_image):
    """Test ML service with real WD-Tagger model if available."""
    service = MLTagSuggestionService()
    await service.load_models()

    if service.using_mock:
        pytest.skip("Real model not available, skipping real model test")

    # Run inference on a real image
    suggestions = await service.generate_suggestions(
        str(sample_image),
        min_confidence=0.35,
    )

    assert isinstance(suggestions, list)
    # Should get some suggestions for a white image (may vary)
    assert all("external_tag" in s for s in suggestions)
    assert all("confidence" in s for s in suggestions)
    assert all(s["confidence"] >= 0.35 for s in suggestions)
    assert all(s["model_source"] == "danbooru" for s in suggestions)
    assert all(s["model_version"] == "wd-swinv2-tagger-v3" for s in suggestions)

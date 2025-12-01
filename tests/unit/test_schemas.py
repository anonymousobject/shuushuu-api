"""
Unit tests for Pydantic schemas.

These tests verify schema validation and serialization.
"""

import pytest
from pydantic import ValidationError

from app.schemas.image import ImageBase, ImageResponse


@pytest.mark.unit
class TestImageSchemas:
    """Tests for image schemas."""

    def test_image_base_valid(self):
        """Test ImageBase with valid data."""
        data = {
            "filename": "test-image",
            "ext": "jpg",
            "md5_hash": "d41d8cd98f00b204e9800998ecf8427e",
            "filesize": 12345,
            "width": 1920,
            "height": 1080,
        }
        image = ImageBase(**data)
        assert image.filename == "test-image"
        assert image.ext == "jpg"
        assert image.width == 1920
        assert image.md5_hash == "d41d8cd98f00b204e9800998ecf8427e"

    def test_image_base_optional_fields(self):
        """Test ImageBase with optional fields."""
        data = {
            "ext": "png",
            "md5_hash": "abc123def456",
            "filesize": 54321,
            "width": 800,
            "height": 600,
        }
        image = ImageBase(**data)
        assert image.filename is None
        assert image.ext == "png"
        assert image.md5_hash == "abc123def456"

    def test_image_response_with_id(self):
        """Test ImageResponse includes image_id."""
        data = {
            "image_id": 123,
            "filename": "test",
            "ext": "jpg",
            "md5_hash": "test123hash",
            "filesize": 1000,
            "width": 100,
            "height": 100,
            "rating": 0.0,
            "user_id": 1,
            "date_added": "2024-01-01T00:00:00",
            "status": 1,
            "locked": 0,
            "posts": 0,
            "favorites": 0,
            "bayesian_rating": 0.0,
            "num_ratings": 0,
            "medium": 0,
            "large": 0,
        }
        image = ImageResponse(**data)
        assert image.image_id == 123
        assert image.filename == "test"
        assert image.md5_hash == "test123hash"

    def test_image_base_invalid_data(self):
        """Test ImageBase validation with invalid data."""
        # Missing required field 'ext'
        with pytest.raises(ValidationError) as exc_info:
            ImageBase(filename="test", filesize=100)

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("ext",) for error in errors)

    def test_image_dimensions_validation(self):
        """Test image dimensions are positive integers."""
        data = {
            "ext": "jpg",
            "md5_hash": "test_hash",
            "filesize": 1000,
            "width": 1920,
            "height": 1080,
        }
        image = ImageBase(**data)
        assert image.width > 0
        assert image.height > 0

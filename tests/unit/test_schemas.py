"""
Unit tests for Pydantic schemas.

These tests verify schema validation and serialization.
"""

import pytest
from pydantic import ValidationError

from app.schemas.image import ImageBase, ImageResponse
from app.schemas.user import UserResponse


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


@pytest.mark.unit
class TestUserSchemas:
    """Tests for user schemas."""

    def test_plain_text_storage_with_none_values(self):
        """Test None values are handled correctly in plain text fields."""
        data = {
            "user_id": 1,
            "username": "testuser",
            "active": True,
            "admin": False,
            "posts": 0,
            "favorites": 0,
            "image_posts": 0,
            "interests": None,
            "location": None,
            "website": None,
            "user_title": None,
        }
        user = UserResponse(**data)
        assert user.interests is None
        assert user.location is None
        assert user.website is None
        assert user.user_title is None

    def test_plain_text_storage_with_empty_strings(self):
        """Test empty strings are handled correctly in plain text fields."""
        data = {
            "user_id": 1,
            "username": "testuser",
            "active": True,
            "admin": False,
            "posts": 0,
            "favorites": 0,
            "image_posts": 0,
            "interests": "",
            "location": "",
            "website": "",
            "user_title": "",
        }
        user = UserResponse(**data)
        # Empty strings should be returned as empty strings
        assert user.interests == ""
        assert user.location == ""
        assert user.website == ""
        assert user.user_title == ""

    def test_fields_without_html_entities_unchanged(self):
        """Test fields without HTML entities are passed through unchanged."""
        data = {
            "user_id": 1,
            "username": "testuser",
            "active": True,
            "admin": False,
            "posts": 0,
            "favorites": 0,
            "image_posts": 0,
            "interests": "Plain text with no entities",
            "location": "Simple Location",
            "website": "https://example.com/path",
            "user_title": "Regular Title",
        }
        user = UserResponse(**data)
        assert user.interests == "Plain text with no entities"
        assert user.location == "Simple Location"
        assert user.website == "https://example.com/path"
        assert user.user_title == "Regular Title"

    def test_int_to_bool_conversion_for_active(self):
        """Test database int (0/1) is converted to boolean for active field."""
        data = {
            "user_id": 1,
            "username": "testuser",
            "active": 1,
            "admin": 0,
            "posts": 0,
            "favorites": 0,
            "image_posts": 0,
        }
        user = UserResponse(**data)
        assert user.active is True
        assert user.admin is False

    def test_int_to_bool_conversion_for_admin(self):
        """Test database int (0/1) is converted to boolean for admin field."""
        data = {
            "user_id": 1,
            "username": "testuser",
            "active": 0,
            "admin": 1,
            "posts": 0,
            "favorites": 0,
            "image_posts": 0,
        }
        user = UserResponse(**data)
        assert user.active is False
        assert user.admin is True

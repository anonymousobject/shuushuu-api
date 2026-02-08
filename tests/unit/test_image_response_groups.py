"""Tests for ImageDetailedResponse groups support."""

from unittest.mock import MagicMock
from datetime import datetime

from app.schemas.image import ImageDetailedResponse


def _create_mock_image(
    user_id: int = 1, username: str = "testuser", user_groups: list[str] | None = None
):
    """Create a mock image object for testing.

    Args:
        user_id: User ID for the mock user
        username: Username for the mock user
        user_groups: List of group names (simulates eager-loaded groups property)
    """
    mock_image = MagicMock()
    mock_image.image_id = 1
    mock_image.filename = "test"
    mock_image.ext = "jpg"
    mock_image.original_filename = "test.jpg"
    mock_image.md5_hash = "abc123"
    mock_image.filesize = 1000
    mock_image.width = 100
    mock_image.height = 100
    mock_image.caption = "Test"
    mock_image.miscmeta = None
    mock_image.rating = 0.0
    mock_image.user_id = user_id
    mock_image.date_added = datetime.now()
    mock_image.status = 1
    mock_image.locked = 0
    mock_image.posts = 0
    mock_image.favorites = 0
    mock_image.bayesian_rating = 0.0
    mock_image.num_ratings = 0
    mock_image.medium = 0
    mock_image.large = 0
    mock_image.replacement_id = None

    # Mock user relationship with groups property (simulates eager-loaded relationship)
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_user.groups = user_groups if user_groups is not None else []
    mock_image.user = mock_user

    # No tags
    mock_image.tag_links = []

    return mock_image


def test_from_db_model_without_groups():
    """from_db_model with user having empty groups."""
    mock_image = _create_mock_image()
    response = ImageDetailedResponse.from_db_model(mock_image)
    assert response.user is not None
    assert response.user.groups == []


def test_from_db_model_with_groups():
    """from_db_model with eager-loaded user groups."""
    mock_image = _create_mock_image(user_id=1, user_groups=["mods", "admins"])
    response = ImageDetailedResponse.from_db_model(mock_image)
    assert response.user is not None
    assert response.user.groups == ["mods", "admins"]

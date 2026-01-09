"""Tests for ImageDetailedResponse groups support."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime

from app.schemas.image import ImageDetailedResponse


def _create_mock_image(user_id: int = 1, username: str = "testuser"):
    """Create a mock image object for testing."""
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

    # Mock user relationship
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_image.user = mock_user

    # No tags
    mock_image.tag_links = []

    return mock_image


def test_from_db_model_without_groups():
    """from_db_model without groups_by_user uses empty groups."""
    mock_image = _create_mock_image()
    response = ImageDetailedResponse.from_db_model(mock_image)
    assert response.user is not None
    assert response.user.groups == []


def test_from_db_model_with_groups():
    """from_db_model with groups_by_user populates user groups."""
    mock_image = _create_mock_image(user_id=1)
    groups_by_user = {1: ["mods", "admins"]}
    response = ImageDetailedResponse.from_db_model(mock_image, groups_by_user=groups_by_user)
    assert response.user is not None
    assert response.user.groups == ["mods", "admins"]


def test_from_db_model_user_not_in_groups_dict():
    """from_db_model with groups_by_user but user not in dict gets empty groups."""
    mock_image = _create_mock_image(user_id=99)
    groups_by_user = {1: ["mods"]}  # User 99 not in dict
    response = ImageDetailedResponse.from_db_model(mock_image, groups_by_user=groups_by_user)
    assert response.user is not None
    assert response.user.groups == []

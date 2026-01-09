"""Tests for CommentResponse groups support."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from app.schemas.comment import CommentResponse, build_comment_response


def _create_mock_comment(user_id: int = 1, username: str = "testuser"):
    """Create a mock comment object for testing."""
    mock_comment = MagicMock()
    mock_comment.post_id = 1
    mock_comment.image_id = 1
    mock_comment.user_id = user_id
    mock_comment.post_text = "Test comment"
    mock_comment.date = datetime.now()
    mock_comment.update_count = 0
    mock_comment.last_updated = None
    mock_comment.last_updated_user_id = None
    mock_comment.parent_comment_id = None
    mock_comment.deleted = False

    # Mock user relationship
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_comment.user = mock_user

    return mock_comment


def test_build_comment_response_without_groups():
    """build_comment_response without groups uses empty list."""
    mock_comment = _create_mock_comment()
    response = build_comment_response(mock_comment)
    assert response.user.groups == []


def test_build_comment_response_with_groups():
    """build_comment_response with groups populates user groups."""
    mock_comment = _create_mock_comment(user_id=1)
    groups_by_user = {1: ["mods"]}
    response = build_comment_response(mock_comment, groups_by_user=groups_by_user)
    assert response.user.groups == ["mods"]


def test_build_comment_response_user_not_in_groups_dict():
    """build_comment_response with groups_by_user but user not in dict gets empty groups."""
    mock_comment = _create_mock_comment(user_id=99)
    groups_by_user = {1: ["mods"]}  # User 99 not in dict
    response = build_comment_response(mock_comment, groups_by_user=groups_by_user)
    assert response.user.groups == []

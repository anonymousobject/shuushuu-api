"""Tests for CommentResponse groups support."""

from datetime import datetime
from unittest.mock import MagicMock

from app.schemas.comment import CommentResponse, build_comment_response


def _create_mock_comment(
    user_id: int = 1, username: str = "testuser", groups: list[str] | None = None
):
    """Create a mock comment object for testing.

    The mock simulates a comment with user relationship eager loaded,
    including the User.groups property that returns group names.
    """
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

    # Mock user relationship with groups property
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_user.groups = groups if groups is not None else []
    mock_comment.user = mock_user

    return mock_comment


def test_build_comment_response_without_groups():
    """build_comment_response without groups uses empty list."""
    mock_comment = _create_mock_comment(groups=[])
    response = build_comment_response(mock_comment)
    assert response.user.groups == []


def test_build_comment_response_with_groups():
    """build_comment_response with groups populates user groups from User.groups property."""
    mock_comment = _create_mock_comment(user_id=1, groups=["mods"])
    response = build_comment_response(mock_comment)
    assert response.user.groups == ["mods"]


def test_build_comment_response_multiple_groups():
    """build_comment_response handles users with multiple groups."""
    mock_comment = _create_mock_comment(user_id=1, groups=["mods", "admins"])
    response = build_comment_response(mock_comment)
    assert response.user.groups == ["mods", "admins"]

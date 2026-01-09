"""Tests for UserSummary groups field."""

from app.schemas.common import UserSummary


def test_user_summary_groups_default_empty():
    """UserSummary should have empty groups list by default."""
    summary = UserSummary(user_id=1, username="testuser")
    assert summary.groups == []


def test_user_summary_groups_with_values():
    """UserSummary should accept groups list."""
    summary = UserSummary(user_id=1, username="testuser", groups=["mods", "admins"])
    assert summary.groups == ["mods", "admins"]


def test_user_summary_groups_in_json():
    """UserSummary groups should appear in JSON output."""
    summary = UserSummary(user_id=1, username="testuser", groups=["mods"])
    data = summary.model_dump()
    assert "groups" in data
    assert data["groups"] == ["mods"]

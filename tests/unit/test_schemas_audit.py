"""Tests for audit schemas."""

from datetime import datetime, UTC

from app.schemas.audit import (
    TagAuditLogResponse,
    ImageStatusHistoryResponse,
    TagHistoryResponse,
)


class TestTagAuditLogResponse:
    """Tests for TagAuditLogResponse schema."""

    def test_validates_rename_action(self) -> None:
        """Test schema validates rename action data."""
        data = {
            "id": 1,
            "tag_id": 100,
            "action_type": "rename",
            "old_title": "Old Name",
            "new_title": "New Name",
            "user": {"user_id": 1, "username": "test"},
            "created_at": datetime.now(UTC),
        }
        response = TagAuditLogResponse.model_validate(data)
        assert response.action_type == "rename"
        assert response.old_title == "Old Name"
        assert response.new_title == "New Name"

    def test_validates_source_linked_action(self) -> None:
        """Test schema validates source_linked action data."""
        data = {
            "id": 1,
            "tag_id": 100,
            "action_type": "source_linked",
            "character_tag": {"tag_id": 100, "title": "Cirno"},
            "source_tag": {"tag_id": 200, "title": "Touhou"},
            "user": {"user_id": 1, "username": "test"},
            "created_at": datetime.now(UTC),
        }
        response = TagAuditLogResponse.model_validate(data)
        assert response.action_type == "source_linked"
        assert response.character_tag is not None
        assert response.source_tag is not None


class TestImageStatusHistoryResponse:
    """Tests for ImageStatusHistoryResponse schema."""

    def test_validates_status_change(self) -> None:
        """Test schema validates status change data."""
        data = {
            "id": 1,
            "image_id": 1000,
            "old_status": 1,
            "old_status_label": "active",
            "new_status": -1,
            "new_status_label": "repost",
            "user": {"user_id": 1, "username": "mod"},
            "created_at": datetime.now(UTC),
        }
        response = ImageStatusHistoryResponse.model_validate(data)
        assert response.old_status == 1
        assert response.new_status == -1

    def test_user_can_be_none(self) -> None:
        """Test schema allows null user for hidden statuses."""
        data = {
            "id": 1,
            "image_id": 1000,
            "old_status": -4,
            "old_status_label": "review",
            "new_status": 1,
            "new_status_label": "active",
            "user": None,
            "created_at": datetime.now(UTC),
        }
        response = ImageStatusHistoryResponse.model_validate(data)
        assert response.user is None


class TestTagHistoryResponse:
    """Tests for TagHistoryResponse schema."""

    def test_validates_add_action(self) -> None:
        """Test schema validates tag add action."""
        data = {
            "tag_history_id": 1,
            "image_id": 1000,
            "tag_id": 100,
            "action": "a",
            "user": {"user_id": 1, "username": "tagger"},
            "date": datetime.now(UTC),
        }
        response = TagHistoryResponse.model_validate(data)
        assert response.action == "a"
        assert response.tag_id == 100

    def test_validates_remove_action(self) -> None:
        """Test schema validates tag remove action."""
        data = {
            "tag_history_id": 2,
            "image_id": 1000,
            "tag_id": 100,
            "action": "r",
            "user": {"user_id": 1, "username": "tagger"},
            "date": datetime.now(UTC),
        }
        response = TagHistoryResponse.model_validate(data)
        assert response.action == "r"

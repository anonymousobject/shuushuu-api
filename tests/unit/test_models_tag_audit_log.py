"""Tests for TagAuditLog model."""

from datetime import datetime

import pytest
from sqlmodel import Session, select

from app.models.tag_audit_log import TagAuditLog


class TestTagAuditLogModel:
    """Tests for TagAuditLog model structure."""

    def test_model_has_required_fields(self) -> None:
        """Verify model has all required fields."""
        # Create instance without saving - just test structure
        log = TagAuditLog(
            tag_id=1,
            action_type="rename",
            old_title="Old Name",
            new_title="New Name",
            user_id=1,
        )
        assert log.tag_id == 1
        assert log.action_type == "rename"
        assert log.old_title == "Old Name"
        assert log.new_title == "New Name"
        assert log.user_id == 1

    def test_nullable_fields_default_to_none(self) -> None:
        """Verify nullable fields default to None."""
        log = TagAuditLog(tag_id=1, action_type="rename")
        assert log.old_title is None
        assert log.new_title is None
        assert log.old_type is None
        assert log.new_type is None
        assert log.old_alias_of is None
        assert log.new_alias_of is None
        assert log.old_parent_id is None
        assert log.new_parent_id is None
        assert log.character_tag_id is None
        assert log.source_tag_id is None
        assert log.user_id is None

"""Tests for config constants."""

from app.config import TagAuditActionType


class TestTagAuditActionType:
    """Tests for TagAuditActionType constants."""

    def test_all_action_types_defined(self) -> None:
        """Verify all expected action types are defined."""
        assert TagAuditActionType.RENAME == "rename"
        assert TagAuditActionType.TYPE_CHANGE == "type_change"
        assert TagAuditActionType.ALIAS_SET == "alias_set"
        assert TagAuditActionType.ALIAS_REMOVED == "alias_removed"
        assert TagAuditActionType.PARENT_SET == "parent_set"
        assert TagAuditActionType.PARENT_REMOVED == "parent_removed"
        assert TagAuditActionType.SOURCE_LINKED == "source_linked"
        assert TagAuditActionType.SOURCE_UNLINKED == "source_unlinked"

    def test_all_values_unique(self) -> None:
        """Ensure no duplicate action type values."""
        values = [
            TagAuditActionType.RENAME,
            TagAuditActionType.TYPE_CHANGE,
            TagAuditActionType.ALIAS_SET,
            TagAuditActionType.ALIAS_REMOVED,
            TagAuditActionType.PARENT_SET,
            TagAuditActionType.PARENT_REMOVED,
            TagAuditActionType.SOURCE_LINKED,
            TagAuditActionType.SOURCE_UNLINKED,
        ]
        assert len(values) == len(set(values))

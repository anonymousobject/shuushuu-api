"""Tests for config constants."""

import pytest
from pydantic import ValidationError

from app.config import Settings, TagAuditActionType


class TestIQDBConfig:
    """Tests for IQDB configuration settings."""

    def test_iqdb_upload_threshold_default(self) -> None:
        """IQDB upload threshold defaults to 90.0."""
        s = Settings()
        assert s.IQDB_UPLOAD_THRESHOLD == 90.0

    def test_iqdb_upload_threshold_higher_than_similarity(self) -> None:
        """Upload threshold should be higher than general similarity threshold."""
        s = Settings()
        assert s.IQDB_UPLOAD_THRESHOLD > s.IQDB_SIMILARITY_THRESHOLD


class TestImageNeighborsTimeoutConfig:
    """statement_timeout works in whole milliseconds and 0 disables it."""

    def test_sub_millisecond_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(IMAGE_NEIGHBORS_TIMEOUT_SECONDS=0.0005)

    def test_one_millisecond_timeout_is_accepted(self) -> None:
        assert Settings(IMAGE_NEIGHBORS_TIMEOUT_SECONDS=0.001).IMAGE_NEIGHBORS_TIMEOUT_SECONDS == (
            0.001
        )


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

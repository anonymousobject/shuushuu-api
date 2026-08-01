"""Tests for TagAuditLog model."""

from app.config import TagAuditActionType
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


class TestTagAuditLogLinkFields:
    """The external-link columns and action types added for link history."""

    def test_link_action_types_are_defined(self):
        assert TagAuditActionType.LINK_ADDED == "link_added"
        assert TagAuditActionType.LINK_REMOVED == "link_removed"
        assert TagAuditActionType.LINK_DEAD_MARKED == "link_dead_marked"
        assert TagAuditActionType.LINK_DEAD_CLEARED == "link_dead_cleared"
        assert TagAuditActionType.LINK_ARCHIVE_CHANGED == "link_archive_changed"

    def test_link_columns_default_to_none(self):
        entry = TagAuditLog(tag_id=1, action_type=TagAuditActionType.RENAME)
        assert entry.link_url is None
        assert entry.old_archive_url is None
        assert entry.new_archive_url is None

    def test_link_columns_accept_long_urls(self):
        url = "https://example.com/" + ("a" * 1900)
        entry = TagAuditLog(
            tag_id=1,
            action_type=TagAuditActionType.LINK_ARCHIVE_CHANGED,
            link_url=url,
            old_archive_url=None,
            new_archive_url="https://web.archive.org/web/2026/x",
        )
        assert entry.link_url == url
        assert entry.new_archive_url == "https://web.archive.org/web/2026/x"

    def test_link_url_is_not_a_foreign_key(self):
        # A link_removed entry must outlive the tag_external_links row it
        # describes, so link_url is plain text with no FK constraint.
        column = TagAuditLog.__table__.c.link_url
        assert len(column.foreign_keys) == 0

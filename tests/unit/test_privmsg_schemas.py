"""Tests for privmsg thread schemas."""

from app.schemas.privmsg import PrivmsgCreate, ThreadSummary


class TestPrivmsgCreate:
    def test_thread_id_optional(self):
        """PrivmsgCreate should accept optional thread_id."""
        msg = PrivmsgCreate(to_user_id=1, subject="Hello", message="World")
        assert msg.thread_id is None

    def test_thread_id_provided(self):
        """PrivmsgCreate should accept thread_id when provided."""
        msg = PrivmsgCreate(to_user_id=1, subject="Hello", message="World", thread_id="abc-123")
        assert msg.thread_id == "abc-123"


class TestThreadSummary:
    def test_thread_summary_fields(self):
        """ThreadSummary should contain all required fields."""
        summary = ThreadSummary(
            thread_id="abc-123",
            subject="Hello",
            other_user_id=2,
            other_username="bob",
            other_avatar_url=None,
            other_groups=["mods"],
            latest_message_preview="Hi there...",
            latest_message_date="2026-01-01T00:00:00",
            unread_count=3,
            message_count=5,
        )
        assert summary.thread_id == "abc-123"
        assert summary.unread_count == 3

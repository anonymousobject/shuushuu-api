"""Tests for privmsg thread schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.privmsg import PrivmsgCreate, PrivmsgMessage, ThreadSummary


class TestPrivmsgCreate:
    def test_thread_id_optional(self):
        """PrivmsgCreate should accept optional thread_id."""
        msg = PrivmsgCreate(to_user_id=1, subject="Hello", message="World")
        assert msg.thread_id is None

    def test_thread_id_provided(self):
        """PrivmsgCreate should accept thread_id when provided."""
        msg = PrivmsgCreate(to_user_id=1, subject="Hello", message="World", thread_id="abc-123")
        assert msg.thread_id == "abc-123"

    def test_message_at_length_limit_accepted(self):
        msg = PrivmsgCreate(to_user_id=1, subject="Hello", message="x" * 20000)
        assert len(msg.message) == 20000

    def test_message_over_length_limit_rejected(self):
        with pytest.raises(ValidationError, match="at most 20000 characters"):
            PrivmsgCreate(to_user_id=1, subject="Hello", message="x" * 20001)


class TestPrivmsgMessage:
    def test_stored_text_over_input_limit_accepted(self):
        """The cap is on input only: a legacy message longer than it still renders."""
        msg = PrivmsgMessage(
            privmsg_id=1,
            viewed=0,
            from_user_id=1,
            to_user_id=2,
            subject="Hello",
            text="x" * 65000,
            date="2026-01-01T00:00:00Z",
        )
        assert len(msg.text_html) == 65000


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

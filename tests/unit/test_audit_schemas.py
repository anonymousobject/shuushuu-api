"""Tests for audit trail schema models."""

from datetime import datetime, timezone

import pytest

from app.schemas.audit import TagHistoryResponse


@pytest.mark.unit
class TestTagHistoryResponse:
    def test_tag_history_id_nullable(self):
        """
        Assert that TagHistoryResponse accepts tag_history_id=None.

        Before the schema widening, this test must fail with a
        ValidationError because tag_history_id: int is required.
        After the schema widening, this test must pass.
        """
        # Create a timezone-aware datetime
        tz_aware_dt = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)

        # This should validate successfully
        response = TagHistoryResponse(tag_history_id=None, date=tz_aware_dt)

        # Verify the field is indeed None
        assert response.tag_history_id is None
        assert response.date == tz_aware_dt

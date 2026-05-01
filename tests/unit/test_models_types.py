"""Unit tests for app.models.types.UtcDateTime."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.models.types import UtcDateTime


class TestUtcDateTimeBind:
    """process_bind_param converts Python -> DB value."""

    def test_naive_datetime_raises(self):
        """Naive datetimes must be rejected on bind to prevent ambiguous UTC assumptions."""
        col = UtcDateTime()
        with pytest.raises(TypeError, match="naive datetime"):
            col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0), dialect=None)

    def test_utc_aware_datetime_strips_tzinfo(self):
        col = UtcDateTime()
        result = col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC), dialect=None)
        assert result == datetime(2026, 5, 1, 12, 0, 0)
        assert result.tzinfo is None

    def test_non_utc_aware_datetime_converts_to_utc(self):
        est = timezone(timedelta(hours=-5))
        col = UtcDateTime()
        # 12:00 EST == 17:00 UTC
        result = col.process_bind_param(datetime(2026, 5, 1, 12, 0, 0, tzinfo=est), dialect=None)
        assert result == datetime(2026, 5, 1, 17, 0, 0)
        assert result.tzinfo is None

    def test_none_passthrough(self):
        col = UtcDateTime()
        assert col.process_bind_param(None, dialect=None) is None


class TestUtcDateTimeResult:
    """process_result_value converts DB value -> Python."""

    def test_naive_datetime_becomes_utc_aware(self):
        col = UtcDateTime()
        result = col.process_result_value(datetime(2026, 5, 1, 12, 0, 0), dialect=None)
        assert result == datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_already_aware_passthrough(self):
        col = UtcDateTime()
        aware = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        result = col.process_result_value(aware, dialect=None)
        assert result == aware
        assert result.tzinfo == UTC

    def test_none_passthrough(self):
        col = UtcDateTime()
        assert col.process_result_value(None, dialect=None) is None

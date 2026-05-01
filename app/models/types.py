"""SQLAlchemy column types for the shuushuu-api models.

UtcDateTime: a DateTime variant that round-trips tz-aware datetimes through
MariaDB's tz-naive DATETIME. Stores values as UTC (tzinfo stripped); attaches
tzinfo=UTC on read. Naive datetimes are rejected on bind to avoid ambiguous
"is this UTC or local?" assumptions.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """DATETIME column that always exposes tz-aware UTC datetimes in Python.

    MariaDB's DATETIME stores no tz info. Without this decorator, reads return
    naive datetimes that cannot be compared with `datetime.now(UTC)` without
    raising TypeError. This decorator centralizes the convention "DB stores
    UTC, Python sees aware" so call sites never need to strip or attach
    tzinfo manually.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise TypeError(
                "naive datetime not allowed; pass datetime.now(UTC) or attach tzinfo=UTC"
            )
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

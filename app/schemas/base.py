"""
Base schema with UTC datetime serialization.

Provides UTCDatetime type annotation that serializes datetime
objects with Z suffix indicating UTC timezone.
"""

from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer

# Custom datetime type that serializes with Z suffix for UTC
# Usage: date: UTCDatetime instead of date: datetime
UTCDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,
        return_type=str,
    ),
]

# Optional version for nullable datetime fields
UTCDatetimeOptional = Annotated[
    datetime | None,
    PlainSerializer(
        lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,
        return_type=str | None,
    ),
]

"""
Custom JSON response with UTC datetime serialization.

All datetime objects are serialized with 'Z' suffix to indicate UTC,
ensuring frontend can properly parse and convert to local timezone.
"""

import json
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse


class UTCDateTimeEncoder(json.JSONEncoder):
    """JSON encoder that serializes datetime objects with Z suffix."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            # Format as ISO 8601 with Z suffix for UTC
            return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        return super().default(obj)


class UTCJSONResponse(JSONResponse):
    """JSON response that serializes all datetimes with UTC Z suffix."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            cls=UTCDateTimeEncoder,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

# Timezone Handling Design

## Problem

Timestamps need consistent handling across the stack:
- Database stores naive datetimes (MariaDB DATETIME has no timezone)
- API sends datetimes without timezone suffix (e.g., `2026-01-11T16:30:00`)
- Frontend may misinterpret these as local time instead of UTC

Legacy approach stored a `timezone` decimal offset per user for server-side conversion, but this is broken for DST and unnecessary with modern browsers.

## Solution

Store and transmit all timestamps as UTC. Let the frontend handle conversion to user's local timezone using the browser's built-in `Intl.DateTimeFormat` API.

## Changes

### 1. Database Connection - Enforce UTC

**File:** `app/core/database.py`

Add `init_command` to set session timezone on every connection:

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"init_command": "SET time_zone = '+00:00'"},
)
```

This ensures any SQL `NOW()` or `CURRENT_TIMESTAMP` calls also use UTC.

### 2. API Responses - Append Z Suffix

**File:** `app/core/serialization.py` (new)

Create a datetime serializer that appends `Z` to indicate UTC:

```python
from datetime import datetime

def serialize_datetime_utc(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO 8601 with Z suffix indicating UTC."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
```

**File:** `app/schemas/base.py` (new or existing)

Create a base schema class with the datetime serializer configured:

```python
from pydantic import BaseModel, ConfigDict

class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: serialize_datetime_utc},
    )
```

Then have all response schemas inherit from `BaseSchema`.

### 3. Optional: Legacy Field Cleanup

The `users.timezone` decimal field can be:
- Left in place for backward compatibility (no harm)
- Removed in a future migration if desired

No action required for this design.

## Frontend Expectations

All API datetime fields will be ISO 8601 with `Z` suffix:
```json
{
  "date_added": "2026-01-11T16:30:00Z",
  "last_login": "2026-01-10T08:15:30Z"
}
```

Frontend can parse and display in user's timezone:
```javascript
const date = new Date("2026-01-11T16:30:00Z");
const formatted = date.toLocaleString(); // Uses browser's timezone
```

## Testing

1. Verify database connection sets timezone to UTC
2. Verify all API datetime responses include `Z` suffix
3. Verify existing tests still pass (datetime format change may require test updates)

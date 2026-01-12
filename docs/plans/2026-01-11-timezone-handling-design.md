# Timezone Handling Design

**Status:** Implemented

## Problem

Timestamps need consistent handling across the stack:
- Database stores naive datetimes (MariaDB DATETIME has no timezone)
- API sends datetimes without timezone suffix (e.g., `2026-01-11T16:30:00`)
- Frontend may misinterpret these as local time instead of UTC

Legacy approach stored a `timezone` decimal offset per user for server-side conversion, but this is broken for DST and unnecessary with modern browsers.

## Solution

Store and transmit all timestamps as UTC. Let the frontend handle conversion to user's local timezone using the browser's built-in `Intl.DateTimeFormat` API.

## Implementation

### 1. Database Connection - Enforce UTC

**File:** `app/core/database.py`

Added `init_command` to set session timezone on every connection:

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

### 2. API Responses - UTC Type Annotations

**File:** `app/schemas/base.py`

Created type annotations using Pydantic's `PlainSerializer` to append `Z` suffix:

```python
from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer

UTCDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,
        return_type=str,
    ),
]

UTCDatetimeOptional = Annotated[
    datetime | None,
    PlainSerializer(
        lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,
        return_type=str | None,
    ),
]
```

**Why type annotations instead of json_encoders?**

Pydantic v2 serializes datetime fields to ISO strings *before* the JSON encoder runs, so `json_encoders` in `model_config` never sees datetime objects. Using `PlainSerializer` in a type annotation intercepts serialization at the right stage.

**Updated schemas:**
- `app/schemas/comment.py` - `CommentResponse.date`
- `app/schemas/user.py` - `UserResponse.date_added`, `last_login`, etc.
- `app/schemas/admin.py` - Audit log datetime fields
- `app/schemas/image.py` - `ImageResponse.date_added`
- `app/schemas/report.py` - Report and review datetime fields
- `app/schemas/tag.py` - `TagWithStats.date_added`, link timestamps

### 3. Legacy Field Cleanup

**Migration:** `alembic/versions/8619a9fc7189_drop_unused_columns.py`

Removed the `users.timezone` field along with other unused columns:
- `users`: timezone, aim, rating_ratio, infected, infected_by, date_infected, last_login_new
- `images`: artist, characters, change_id
- `privmsgs`: type, card, cardpath

## Frontend Expectations

All API datetime fields are ISO 8601 with `Z` suffix:
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

1. Database connection sets timezone to UTC via `init_command`
2. All API datetime responses include `Z` suffix (verified manually via curl)
3. Existing tests pass with datetime format changes

# Permission Sync: Code as Source of Truth

## Problem

Permissions are currently defined in two places:

1. **Code** (`app/core/permissions.py`): `Permission` enum with type-safe constants
2. **Database** (`perms` table): Stores permissions with `perm_id`, `title`, `desc`

This duplication creates:
- Sync anxiety: enum and database can drift apart
- Maintenance burden: adding permissions requires changes in two places
- Unclear authority: which is the source of truth?

## Solution

Make the code the single source of truth. The `Permission` enum defines what permissions exist; the database is seeded from it on startup.

**Key distinction:**
- **What permissions exist** → Code (Permission enum)
- **Who has what permissions** → Database (user_perms, group_perms, user_groups)

## Design

### 1. Enum with Descriptions

The `Permission` enum becomes the complete source of truth, including human-readable descriptions:

```python
# app/core/permissions.py

class Permission(str, Enum):
    """
    Type-safe permission constants mapped to database perm titles.
    The enum is the source of truth - database is seeded from this on startup.
    """

    # Tag management
    TAG_CREATE = "tag_create"
    TAG_EDIT = "tag_edit"
    TAG_UPDATE = "tag_update"
    TAG_DELETE = "tag_delete"

    # Image management
    IMAGE_EDIT_META = "image_edit_meta"
    IMAGE_EDIT = "image_edit"
    IMAGE_MARK_REPOST = "image_mark_repost"
    IMAGE_TAG_ADD = "image_tag_add"
    IMAGE_TAG_REMOVE = "image_tag_remove"

    # User/Group management
    GROUP_MANAGE = "group_manage"
    GROUP_PERM_MANAGE = "group_perm_manage"
    USER_EDIT_PROFILE = "user_edit_profile"
    USER_BAN = "user_ban"
    PRIVMSG_VIEW = "privmsg_view"

    # Content moderation
    POST_EDIT = "post_edit"

    # Special permissions
    THEME_EDIT = "theme_edit"
    RATING_REVOKE = "rating_revoke"
    REPORT_REVOKE = "report_revoke"

    # Report & Review system
    REPORT_VIEW = "report_view"
    REPORT_MANAGE = "report_manage"
    REVIEW_VIEW = "review_view"
    REVIEW_START = "review_start"
    REVIEW_VOTE = "review_vote"
    REVIEW_CLOSE_EARLY = "review_close_early"

    @property
    def description(self) -> str:
        """Human-readable description for this permission."""
        descriptions = {
            # Tag management
            Permission.TAG_CREATE: "Create new tags",
            Permission.TAG_EDIT: "Edit existing tags",
            Permission.TAG_UPDATE: "Update tag information",
            Permission.TAG_DELETE: "Delete tags",
            # Image management
            Permission.IMAGE_EDIT_META: "Edit image metadata",
            Permission.IMAGE_EDIT: "Deactivate or delete images",
            Permission.IMAGE_MARK_REPOST: "Mark images as reposts",
            Permission.IMAGE_TAG_ADD: "Add tags to images",
            Permission.IMAGE_TAG_REMOVE: "Remove tags from images",
            # User/Group management
            Permission.GROUP_MANAGE: "Add and edit groups",
            Permission.GROUP_PERM_MANAGE: "Add and edit group permissions",
            Permission.USER_EDIT_PROFILE: "Edit user profiles",
            Permission.USER_BAN: "Ban users and IPs",
            Permission.PRIVMSG_VIEW: "View private messages",
            # Content moderation
            Permission.POST_EDIT: "Edit text posts and comments",
            # Special permissions
            Permission.THEME_EDIT: "Theme editor and scheduler access",
            Permission.RATING_REVOKE: "Revoke image rating rights",
            Permission.REPORT_REVOKE: "Revoke image reporting rights",
            # Report & Review system
            Permission.REPORT_VIEW: "View report triage queue",
            Permission.REPORT_MANAGE: "Dismiss, action, or escalate reports",
            Permission.REVIEW_VIEW: "View open reviews",
            Permission.REVIEW_START: "Initiate appropriateness review",
            Permission.REVIEW_VOTE: "Cast votes on reviews",
            Permission.REVIEW_CLOSE_EARLY: "Close review before deadline",
        }
        return descriptions.get(self, "")
```

Access patterns:
- `Permission.TAG_CREATE.value` → `"tag_create"` (database title)
- `Permission.TAG_CREATE.description` → `"Create new tags"` (human-readable)

### 2. Startup Sync Logic

New module that syncs the enum to database on application startup:

```python
# app/core/permission_sync.py

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.permissions import Permission
from app.models.permissions import Perms

logger = get_logger(__name__)


async def sync_permissions(db: AsyncSession) -> None:
    """
    Ensure database perms table matches Permission enum.

    - Inserts any permissions in enum but not in DB
    - Warns about orphan permissions in DB but not in enum
    - Idempotent - safe to run on every startup
    """
    enum_titles = {p.value for p in Permission}

    # Get all existing permissions from DB
    result = await db.execute(select(Perms))
    db_perms = {p.title: p for p in result.scalars().all()}
    db_titles = set(db_perms.keys())

    # Insert missing permissions
    missing = enum_titles - db_titles
    for perm in Permission:
        if perm.value in missing:
            db.add(Perms(title=perm.value, desc=perm.description))
            logger.info("permission_seeded", title=perm.value)

    # Warn about orphans (in DB but not in enum)
    orphans = db_titles - enum_titles
    for title in orphans:
        logger.warning(
            "orphan_permission",
            title=title,
            hint="Permission exists in DB but not in code",
        )

    await db.commit()
    logger.info("permissions_synced", total=len(enum_titles), added=len(missing))
```

### 3. FastAPI Lifespan Integration

The sync runs during FastAPI's lifespan startup, before accepting requests:

```python
# app/main.py

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_engine
from app.core.permission_sync import sync_permissions


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: sync permissions
    async with AsyncSession(async_engine) as db:
        await sync_permissions(db)

    yield

    # Shutdown (nothing needed for permissions)


app = FastAPI(lifespan=lifespan)
```

### 4. Error Handling

If permission sync fails, the app fails to start. A misconfigured permissions system is a critical error:

```python
async def sync_permissions(db: AsyncSession) -> None:
    try:
        # ... sync logic ...
        await db.commit()
    except Exception as e:
        logger.error("permission_sync_failed", error=str(e))
        raise  # Crash startup - don't serve with broken permissions
```

### 5. Edge Cases

| Scenario | Behavior |
|----------|----------|
| First run (empty `perms` table) | All permissions inserted |
| Normal restart | No changes, quick no-op |
| New permission added to enum | Single insert |
| Permission removed from enum | Warning logged, row stays in DB |
| DB connection fails | App fails to start |
| Description updated in enum | Existing rows unchanged (use migration to force-update) |

## Files Changed

| File | Change |
|------|--------|
| `app/core/permissions.py` | Add `description` property to `Permission` enum |
| `app/core/permission_sync.py` | New file - sync logic |
| `app/main.py` | Call `sync_permissions()` in lifespan |

## Files Unchanged

- `app/models/permissions.py` - DB models stay the same
- `app/api/v1/admin.py` - Existing endpoints work as-is
- `app/api/v1/permissions.py` - Could optionally include descriptions in response

## Testing

- Unit test for `sync_permissions()` covering:
  - Empty DB (all permissions seeded)
  - Idempotent re-run (no duplicate inserts)
  - Orphan detection (warning logged)
- Integration tests get permissions via existing test fixtures

## Migration

None required. This is purely additive:
- Existing `perms` rows are preserved
- Missing permissions are inserted on startup
- Orphan permissions remain but trigger warnings

# Admin Moderation Endpoints Design

**Date**: 2025-12-30
**Status**: Approved
**Approach**: Minimal - Fill gaps using existing permissions

## Overview

This design addresses three gaps in admin/moderator API functionality:

1. **Direct Image Moderation** - Change image status without going through report system
2. **Admin Comment Deletion** - Allow moderators to delete any user's comments
3. **User Profile Edit Authorization** - Fix permission check to use modern permission system

## Design Decisions

### Approach Selection

Selected **Approach 1: Minimal** over alternatives:
- Reuses existing permissions (`IMAGE_EDIT`, `POST_EDIT`, `USER_EDIT_PROFILE`)
- Follows established patterns in the codebase
- Minimal code changes required

Rejected alternatives:
- Full Admin Namespace: Breaking change, code duplication
- New Permissions: Unnecessary granularity, existing permissions sufficient

---

## Feature 1: Direct Image Moderation

### Endpoint

```
PATCH /admin/images/{image_id}
```

### Permission

`IMAGE_EDIT` - "Deactivate or delete images"

### Request Schema

```python
class ImageStatusUpdate(BaseModel):
    status: int  # -4=Review, -2=Inappropriate, -1=Repost, 0=Other, 1=Active, 2=Spoiler
    replacement_id: int | None = None  # Required when status=-1 (Repost)
```

### Validation Rules

| Rule | Description |
|------|-------------|
| Status values | Must be valid `ImageStatus` constant (-4 to 2, excluding -3) |
| Repost requires original | `replacement_id` required when `status == -1` |
| Valid replacement | `replacement_id` must reference existing image |
| No self-reference | Cannot set `replacement_id` to same `image_id` |
| Clear on status change | Clear `replacement_id` when changing away from Repost status |

### Response Schema

```python
class ImageStatusResponse(BaseModel):
    image_id: int
    status: int
    replacement_id: int | None
    status_user_id: int
    status_updated: datetime
```

### Database Changes

Updates to `images` table:
- `status` - New status value
- `replacement_id` - Original image ID (for reposts) or NULL
- `status_user_id` - Admin who made the change
- `status_updated` - Timestamp of change

### Audit Trail

New `AdminActions` record:
- `action_type`: `IMAGE_STATUS_CHANGE`
- `image_id`: Target image
- `user_id`: Admin performing action
- `details`: `{"previous_status": X, "new_status": Y, "replacement_id": Z}`

---

## Feature 2: Admin Comment Deletion

### Endpoint

```
DELETE /comments/{comment_id}
```

(Existing endpoint, modified behavior)

### Permission

`POST_EDIT` - "Edit text posts and comments"

### Authorization Logic

```python
is_owner = comment.user_id == current_user.user_id
has_mod_permission = await has_permission(db, current_user.user_id, Permission.POST_EDIT, redis_client)

if not is_owner and not has_mod_permission:
    raise HTTPException(status_code=403, detail="You can only delete your own comments")
```

### Behavior

- Owner can always delete their own comments (unchanged)
- Users with `POST_EDIT` can delete any comment (new)
- Same soft-delete mechanism: `deleted=True`, text becomes `[deleted]`

### Audit Trail

Only logged when moderator deletes someone else's comment:
- `action_type`: `COMMENT_DELETE`
- `details`: `{"comment_id": X, "image_id": Y, "original_user_id": Z}`

---

## Feature 3: User Profile Edit Authorization

### Endpoints

All three endpoints receive the same fix:
- `PATCH /users/{user_id}` - Edit profile
- `POST /users/{user_id}/avatar` - Upload avatar
- `DELETE /users/{user_id}/avatar` - Delete avatar

### Permission

`USER_EDIT_PROFILE` - "Edit user profiles"

### Current (Broken) Logic

```python
if current_user.user_id != user_id and not current_user.admin:
    raise HTTPException(status_code=403, ...)
```

Uses legacy `admin` column instead of permission system.

### New Logic

```python
is_self = current_user.user_id == user_id
has_edit_permission = await has_permission(
    db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
)

if not is_self and not has_edit_permission:
    raise HTTPException(status_code=403, detail="Not authorized to update this user")
```

### No Audit Trail

Profile edits are low-risk and don't require admin action logging.

---

## Implementation Details

### New AdminActionType Values

Add to `app/config.py`:

```python
class AdminActionType:
    # ... existing values ...
    IMAGE_STATUS_CHANGE = "image_status_change"
    COMMENT_DELETE = "comment_delete"
```

### New Schemas

Add to `app/schemas/admin.py`:

```python
class ImageStatusUpdate(BaseModel):
    """Request schema for changing image status directly."""
    status: int
    replacement_id: int | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        valid_statuses = {-4, -2, -1, 0, 1, 2}  # Exclude -3 (LOW_QUALITY) if not used
        if v not in valid_statuses:
            raise ValueError(f"Invalid status: {v}")
        return v


class ImageStatusResponse(BaseModel):
    """Response schema for image status change."""
    image_id: int
    status: int
    replacement_id: int | None
    status_user_id: int
    status_updated: datetime

    model_config = {"from_attributes": True}
```

---

## Files Modified

| File | Changes |
|------|---------|
| `app/config.py` | Add `IMAGE_STATUS_CHANGE`, `COMMENT_DELETE` to `AdminActionType` |
| `app/schemas/admin.py` | Add `ImageStatusUpdate`, `ImageStatusResponse` schemas |
| `app/api/v1/admin.py` | Add `PATCH /admin/images/{image_id}` endpoint |
| `app/api/v1/comments.py` | Update `DELETE` to check `POST_EDIT` permission |
| `app/api/v1/users.py` | Replace `admin` flag checks with `USER_EDIT_PROFILE` permission (3 endpoints) |

## Tests Required

| File | Tests |
|------|-------|
| `tests/api/v1/test_admin_images.py` | New file: status changes, repost linking, validation, permissions |
| `tests/api/v1/test_comments.py` | Add: mod deletion, permission checks, audit logging |
| `tests/api/v1/test_users.py` | Update: authorization tests for permission-based access |

---

## Security Considerations

1. **Permission-based access**: All endpoints use the modern permission system with Redis caching
2. **Audit trails**: Admin actions logged for accountability
3. **Input validation**: Status values validated against allowed constants
4. **Self-reference prevention**: Cannot mark image as repost of itself

## Migration Notes

- No database migrations required (uses existing columns)
- No breaking API changes (new endpoint + relaxed permissions on existing endpoints)
- Existing `admin` column on users table becomes effectively unused for these features

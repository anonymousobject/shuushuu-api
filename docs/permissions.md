# Permission System

The permission system provides granular access control for elevated user permissions (moderator, admin, and special capabilities).

## Overview

- **Binary permissions**: Users either have a permission or they don't
- **Group-based + user overrides**: Permissions assigned through groups, with optional direct user assignments
- **FastAPI dependencies**: Type-safe, declarative route protection
- **Existing schema**: Uses the legacy PHP database tables (no schema changes needed)

## Architecture

```
Users → UserGroups → Groups → GroupPerms → Perms
             ↓                                  ↑
             └────────→ UserPerms ──────────────┘
```

- **Perms**: Permission definitions (editimg, createtag, ban, etc.)
- **Groups**: User groups (moderators, taggers, admins)
- **GroupPerms**: Permissions assigned to groups
- **UserGroups**: Users assigned to groups
- **UserPerms**: Direct permission overrides for specific users

## Available Permissions

All permissions are defined in `app.core.permissions.Permission`:

### Tag Management
- `CREATE_TAG` - Create new tags
- `EDIT_TAG` - Edit existing tags
- `RENAME_TAG` - Rename tags
- `DELETE_TAG` - Delete tags

### Image Management
- `EDIT_IMG_META` - Edit image metadata
- `EDIT_IMG_FILENAME` - Edit image filenames
- `EDIT_IMG` - Edit/deactivate/delete images
- `CHECK_DUPES` - Check for duplicates
- `REPOST` - Mark reposts

### User/Group Management
- `ALL_GROUP` - Add/edit groups
- `ALL_GROUP_PERM` - Add/edit group permissions
- `EDIT_PROFILE` - Edit user profiles
- `BAN` - Ban users/IPs

### Content Moderation
- `EDIT_POST` - Edit text posts

### Access Levels
- `TAGGER_LEVEL` - Tagger level access
- `MOD_LEVEL` - Moderator level access
- `ADMIN_LEVEL` - Administrator level access

### Special Permissions
- `THEME_EDITOR` - Theme editor/scheduler access
- `REVOKE_RATING` - Revoke image rating rights
- `REVOKE_REPORTS` - Revoke image reporting rights

## Usage in Routes

### Basic Permission Check

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from app.core.auth import CurrentUser
from app.core.permission_deps import require_permission
from app.core.permissions import Permission

router = APIRouter()

@router.delete("/images/{image_id}")
async def delete_image(
    image_id: int,
    user: CurrentUser,
    _: Annotated[None, Depends(require_permission(Permission.EDIT_IMG))]
):
    """
    Delete an image.

    Requires: editimg permission
    """
    # User definitely has editimg permission here
    # Implementation...
```

### Multiple Permission Options (Any)

```python
from app.core.permission_deps import require_any_permission

@router.post("/images/{image_id}/tag")
async def tag_image(
    image_id: int,
    user: CurrentUser,
    _: Annotated[None, Depends(require_any_permission([
        Permission.CREATE_TAG,
        Permission.TAGGER_LEVEL,
        Permission.MOD_LEVEL
    ]))]
):
    """
    Add a tag to an image.

    Requires: createtag OR taggerlevel OR modlevel
    """
    # User has at least one of the permissions
    # Implementation...
```

### Multiple Required Permissions (All)

```python
from app.core.permission_deps import require_all_permissions

@router.post("/groups/{group_id}/permissions")
async def modify_group_permissions(
    group_id: int,
    user: CurrentUser,
    _: Annotated[None, Depends(require_all_permissions([
        Permission.ALL_GROUP,
        Permission.ALL_GROUP_PERM
    ]))]
):
    """
    Modify permissions for a group.

    Requires: allgroup AND allgroupperm
    """
    # User has both permissions
    # Implementation...
```

### Convenience Type Aliases

For common permissions, use the pre-defined type aliases:

```python
from app.core.permission_deps import RequireEditImg, RequireBan, RequireModLevel

@router.delete("/images/{image_id}")
async def delete_image(
    image_id: int,
    user: CurrentUser,
    _: RequireEditImg  # Shorthand for require_permission(Permission.EDIT_IMG)
):
    # Implementation...
```

Available aliases:
- `RequireEditImg`
- `RequireCreateTag`
- `RequireEditTag`
- `RequireBan`
- `RequireModLevel`
- `RequireAdminLevel`

## Manual Permission Checking

For business logic that needs to check permissions:

```python
from app.core.permissions import has_permission, get_user_permissions

# Check single permission
if await has_permission(db, user_id, Permission.EDIT_IMG):
    # User can edit images
    ...

# Check multiple permissions
if await has_any_permission(db, user_id, [Permission.EDIT_IMG, Permission.MOD_LEVEL]):
    # User is a moderator or can edit images
    ...

if await has_all_permissions(db, user_id, [Permission.ALL_GROUP, Permission.ALL_GROUP_PERM]):
    # User can manage groups and permissions
    ...

# Get all permissions for a user
permissions = await get_user_permissions(db, user_id)
# Returns: {"editimg", "ban", "modlevel", ...}
```

## Error Responses

When a user lacks required permissions, they receive:

```json
{
  "detail": "Insufficient permissions. Requires permission: editimg"
}
```

Or for multiple permissions:

```json
{
  "detail": "Insufficient permissions. Requires one of: createtag, taggerlevel, modlevel"
}
```

```json
{
  "detail": "Insufficient permissions. Missing: allgroupperm"
}
```

HTTP status code: **403 Forbidden**

## Permission Resolution

Permissions are resolved from the database in a single query that combines:

1. **Group permissions**: Via `user_groups` → `group_perms` → `perms`
2. **Direct user permissions**: Via `user_perms` → `perms`

The query automatically handles:
- Multiple group memberships
- Permission overrides
- Inactive permissions (`permvalue=0` are excluded)

## Performance Considerations

### Current Implementation
- Single database query per request resolves all permissions
- No caching (simple, consistent)
- Acceptable for most workloads

### Future Optimization (Phase 3)
When performance becomes a concern:
- Add Redis caching for permission sets
- Cache key: `permissions:user:{user_id}`
- TTL: 5-15 minutes
- Invalidate on permission changes

## Database Management

### Viewing Permissions

```sql
-- All permissions
SELECT * FROM perms;

-- User's groups
SELECT g.* FROM groups g
JOIN user_groups ug ON g.group_id = ug.group_id
WHERE ug.user_id = ?;

-- Group's permissions
SELECT p.* FROM perms p
JOIN group_perms gp ON p.perm_id = gp.perm_id
WHERE gp.group_id = ? AND gp.permvalue = 1;

-- User's direct permissions
SELECT p.* FROM perms p
JOIN user_perms up ON p.perm_id = up.perm_id
WHERE up.user_id = ? AND up.permvalue = 1;
```

### Adding Permissions

```python
# Add user to group
db.add(UserGroups(user_id=123, group_id=2))  # 2 = moderators

# Add direct permission
db.add(UserPerms(user_id=123, perm_id=1, permvalue=1))  # 1 = createtag

# Add permission to group
db.add(GroupPerms(group_id=2, perm_id=8, permvalue=1))  # 8 = editimg
```

## Migration from user.admin

The legacy `users.admin` field is currently used by `app.core.auth.require_admin()`.

**Migration plan:**
1. ✅ Phase 1: Permission system implemented (current)
2. Phase 2: Replace `require_admin()` with `require_permission(Permission.ADMIN_LEVEL)`
3. Phase 3: Remove `user.admin` field entirely, migrate data to permissions

## Testing

Comprehensive tests are in `tests/test_permissions.py`:

```bash
# Run all permission tests
pytest tests/test_permissions.py -v

# Run specific test class
pytest tests/test_permissions.py::TestPermissionResolution -v
```

Test coverage:
- ✅ Permission resolution from groups
- ✅ Permission resolution from direct assignments
- ✅ Combined permissions (groups + overrides)
- ✅ Permission checking functions
- ✅ FastAPI dependencies
- ✅ Error handling

## Example: Converting an Existing Route

**Before (using admin flag):**

```python
from app.core.auth import require_admin

@router.delete("/images/{id}")
async def delete_image(image_id: int, admin: Annotated[Users, Depends(require_admin)]):
    # Implementation...
```

**After (using permissions):**

```python
from app.core.permission_deps import require_permission
from app.core.permissions import Permission

@router.delete("/images/{id}")
async def delete_image(
    image_id: int,
    user: CurrentUser,
    _: Annotated[None, Depends(require_permission(Permission.EDIT_IMG))]
):
    # Implementation...
```

**Benefits:**
- More granular control (not just admin/non-admin)
- Type-safe permission names
- Better error messages
- Easier to audit and test

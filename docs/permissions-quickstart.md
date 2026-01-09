# Permission System - Quick Start

## TL;DR

```python
from typing import Annotated
from fastapi import Depends
from app.core.auth import CurrentUser
from app.core.permission_deps import require_permission
from app.core.permissions import Permission

@router.delete("/images/{id}")
async def delete_image(
    image_id: int,
    user: CurrentUser,
    _: Annotated[None, Depends(require_permission(Permission.EDIT_IMG))]
):
    """Requires editimg permission."""
    # User has permission, proceed...
```

## Common Patterns

### Single Permission Required

```python
# Using enum (recommended - type-safe)
_: Annotated[None, Depends(require_permission(Permission.EDIT_IMG))]

# Using string (also works)
_: Annotated[None, Depends(require_permission("editimg"))]

# Using convenience alias
from app.core.permission_deps import RequireEditImg
_: RequireEditImg
```

### Multiple Permissions (ANY)

```python
from app.core.permission_deps import require_any_permission

_: Annotated[None, Depends(require_any_permission([
    Permission.CREATE_TAG,
    Permission.TAGGER_LEVEL,
    Permission.MOD_LEVEL
]))]
# User needs at least ONE of these
```

### Multiple Permissions (ALL)

```python
from app.core.permission_deps import require_all_permissions

_: Annotated[None, Depends(require_all_permissions([
    Permission.ALL_GROUP,
    Permission.ALL_GROUP_PERM
]))]
# User needs BOTH of these
```

## Available Permissions

```python
# Tag management
Permission.CREATE_TAG        # createtag
Permission.EDIT_TAG          # edittag
Permission.RENAME_TAG        # renametag
Permission.DELETE_TAG        # deletetag

# Image management
Permission.EDIT_IMG_META     # editimgmeta
Permission.EDIT_IMG_FILENAME # editimgfilename
Permission.EDIT_IMG          # editimg
Permission.CHECK_DUPES       # checkdupes
Permission.REPOST            # repost

# User/Group management
Permission.ALL_GROUP         # allgroup
Permission.ALL_GROUP_PERM    # allgroupperm
Permission.EDIT_PROFILE      # editprofile
Permission.BAN               # ban

# Content moderation
Permission.EDIT_POST         # editpost

# Access levels
Permission.TAGGER_LEVEL      # taggerlevel
Permission.MOD_LEVEL         # modlevel
Permission.ADMIN_LEVEL       # adminlevel

# Special
Permission.THEME_EDITOR      # themeeditor
Permission.REVOKE_RATING     # revokerating
Permission.REVOKE_REPORTS    # revokereports
```

## Manual Checks (Business Logic)

```python
from app.core.permissions import has_permission, get_user_permissions

# In async function
if await has_permission(db, user.user_id, Permission.EDIT_IMG):
    # Do something

# Get all permissions
permissions = await get_user_permissions(db, user.user_id)
if "editimg" in permissions:
    # Do something
```

## Error Response

User without permission gets **403 Forbidden**:

```json
{
  "detail": "Insufficient permissions. Requires permission: editimg"
}
```

## Database Operations

### Assign User to Group

```python
from app.models.permissions import UserGroups

# Add user to moderators group (group_id=2)
db.add(UserGroups(user_id=123, group_id=2))
await db.commit()
```

### Assign Direct Permission

```python
from app.models.permissions import UserPerms

# Give user createtag permission (perm_id=1)
db.add(UserPerms(user_id=123, perm_id=1, permvalue=1))
await db.commit()
```

### Check Existing Permissions (SQL)

```sql
-- See user's groups
SELECT g.title FROM groups g
JOIN user_groups ug ON g.group_id = ug.group_id
WHERE ug.user_id = 123;

-- See user's effective permissions (from groups)
SELECT DISTINCT p.title FROM perms p
JOIN group_perms gp ON p.perm_id = gp.perm_id
JOIN user_groups ug ON gp.group_id = ug.group_id
WHERE ug.user_id = 123 AND gp.permvalue = 1;

-- See user's direct permissions
SELECT p.title FROM perms p
JOIN user_perms up ON p.perm_id = up.perm_id
WHERE up.user_id = 123 AND up.permvalue = 1;
```

## Example Routes

### Delete Image (Moderator Only)

```python
@router.delete("/images/{image_id}", status_code=204)
async def delete_image(
    image_id: int,
    user: CurrentUser,
    _: RequireEditImg,  # Shorthand
    db: Annotated[AsyncSession, Depends(get_db)]
):
    """Delete an image. Requires editimg permission."""
    result = await db.execute(select(Images).where(Images.image_id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(404, "Image not found")

    await db.delete(image)
    await db.commit()
```

### Create Tag (Taggers or Higher)

```python
@router.post("/tags", response_model=TagResponse)
async def create_tag(
    tag_data: TagCreate,
    user: CurrentUser,
    _: Annotated[None, Depends(require_any_permission([
        Permission.CREATE_TAG,
        Permission.TAGGER_LEVEL,
        Permission.MOD_LEVEL
    ]))],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    """Create a new tag. Requires createtag, taggerlevel, or modlevel."""
    new_tag = Tags(**tag_data.model_dump())
    db.add(new_tag)
    await db.commit()
    await db.refresh(new_tag)
    return new_tag
```

### Ban User (Admin Only)

```python
@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    ban_data: BanCreate,
    user: CurrentUser,
    _: RequireBan,  # Shorthand for require_permission(Permission.BAN)
    db: Annotated[AsyncSession, Depends(get_db)]
):
    """Ban a user. Requires ban permission."""
    # Implementation...
```

## Running Tests

```bash
# All permission tests
uv run pytest tests/test_permissions.py -v

# Specific test
uv run pytest tests/test_permissions.py::TestPermissionResolution::test_get_permissions_from_group -v
```

## Next Steps

1. **Use in routes**: Add permission checks to existing endpoints
2. **Test**: Verify permissions work as expected
3. **Migrate from admin flag**: Replace `require_admin()` with `require_permission(Permission.ADMIN_LEVEL)`

See [docs/permissions.md](./permissions.md) for complete documentation.

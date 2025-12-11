# Migrating from user.admin to Permission System

This document shows how to migrate existing routes from the `user.admin` flag to the new permission system.

## Example 1: Tag Editing (app/api/v1/tags.py:291)

### Before

```python
@router.put("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: int,
    tag_data: TagUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a tag."""
    if not current_user.admin:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required"
        )

    # Update tag logic...
```

### After (Simple - Admin Only)

```python
from app.core.permission_deps import require_permission
from app.core.permissions import Permission

@router.put("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: int,
    tag_data: TagUpdate,
    current_user: CurrentUser,
    _: Annotated[None, Depends(require_permission(Permission.EDIT_TAG))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Update a tag.

    Requires: edittag permission
    """
    # Update tag logic...
    # No need to check current_user.admin - dependency already did it
```

### After (Better - Multiple Permission Levels)

```python
@router.put("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: int,
    tag_data: TagUpdate,
    current_user: CurrentUser,
    _: Annotated[None, Depends(require_any_permission([
        Permission.EDIT_TAG,      # Dedicated tag editors
        Permission.MOD_LEVEL,     # Moderators
        Permission.ADMIN_LEVEL    # Admins
    ]))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Update a tag.

    Requires: edittag OR modlevel OR adminlevel
    """
    # Update tag logic...
```

## Example 2: Image Editing with Ownership Check (app/api/v1/images.py:420)

### Before

```python
@router.put("/images/{image_id}", response_model=ImageResponse)
async def update_image(
    image_id: int,
    image_data: ImageUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update an image."""
    result = await db.execute(select(Images).where(Images.image_id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(404, "Image not found")

    # Check ownership or admin
    if image.user_id != current_user.user_id and not current_user.admin:
        raise HTTPException(403, "Not authorized to edit this image")

    # Update logic...
```

### After (Keep Ownership Check, Add Permission for Others)

```python
from app.core.permissions import has_permission, Permission

@router.put("/images/{image_id}", response_model=ImageResponse)
async def update_image(
    image_id: int,
    image_data: ImageUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Update an image.

    Users can edit their own images.
    Moderators with editimg permission can edit any image.
    """
    result = await db.execute(select(Images).where(Images.image_id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(404, "Image not found")

    # Check ownership or permission
    is_owner = image.user_id == current_user.user_id
    has_edit_permission = await has_permission(db, current_user.user_id, Permission.EDIT_IMG)

    if not is_owner and not has_edit_permission:
        raise HTTPException(403, "Not authorized to edit this image")

    # Update logic...
```

**Note**: This example shows that ownership checks and permissions serve different purposes:
- **Ownership**: Basic user permissions on their own content
- **Permissions**: Elevated permissions for moderators/admins

## Example 3: Strict Admin-Only Route (app/api/v1/privmsgs.py:42)

### Before

```python
@router.get("/privmsgs", response_model=List[PrivMsgResponse])
async def list_all_messages(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all private messages (admin only)."""
    if not current_user.admin:
        raise HTTPException(403, "Admin privileges required")

    # List all messages...
```

### After

```python
from app.core.permission_deps import RequireAdminLevel

@router.get("/privmsgs", response_model=List[PrivMsgResponse])
async def list_all_messages(
    current_user: CurrentUser,
    _: RequireAdminLevel,  # Convenience alias
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    List all private messages.

    Requires: adminlevel permission
    """
    # List all messages...
    # No need to check current_user.admin
```

## Example 4: User Profile Editing (app/api/v1/users.py:110)

### Before

```python
@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a user profile."""
    # Users can edit their own profile, admins can edit anyone's
    if current_user.user_id != user_id and not current_user.admin:
        raise HTTPException(403, "Not authorized to edit this profile")

    # Update logic...
```

### After

```python
from app.core.permissions import has_permission, Permission

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Update a user profile.

    Users can edit their own profile.
    Users with editprofile permission can edit any profile.
    """
    # Check if user is editing their own profile or has permission
    is_own_profile = current_user.user_id == user_id
    can_edit_others = await has_permission(db, current_user.user_id, Permission.EDIT_PROFILE)

    if not is_own_profile and not can_edit_others:
        raise HTTPException(403, "Not authorized to edit this profile")

    # Update logic...
```

## Migration Checklist

For each route currently using `current_user.admin`:

1. **Identify the action being protected**
   - What permission best describes it? (editimg, ban, modlevel, etc.)

2. **Decide on permission strategy**
   - Single permission: `require_permission(Permission.X)`
   - Multiple options: `require_any_permission([Permission.X, Permission.Y])`
   - Multiple required: `require_all_permissions([Permission.X, Permission.Y])`
   - Ownership + permission: Use `has_permission()` in route logic

3. **Update the route**
   - Add import: `from app.core.permission_deps import require_permission`
   - Add dependency: `_: Annotated[None, Depends(require_permission(Permission.X))]`
   - Remove: `if not current_user.admin: raise HTTPException(403, ...)`
   - Update docstring to document required permission

4. **Test**
   - Test with user who has permission → should work
   - Test with user without permission → should get 403
   - Test ownership scenarios if applicable

5. **Update database**
   - Ensure relevant groups have the permission
   - Ensure admins have `adminlevel` permission
   - Ensure moderators have `modlevel` permission

## Database Setup After Migration

Once routes use permissions instead of `user.admin`, ensure permissions are assigned:

```sql
-- Give admin users the adminlevel permission
-- Assuming group_id=3 is admins and perm_id=10 is adminlevel
INSERT INTO group_perms (group_id, perm_id, permvalue)
VALUES (3, 10, 1)
ON DUPLICATE KEY UPDATE permvalue=1;

-- Or assign directly to specific users
INSERT INTO user_perms (user_id, perm_id, permvalue)
VALUES (2, 10, 1)  -- User 2 gets adminlevel
ON DUPLICATE KEY UPDATE permvalue=1;
```

## Testing After Migration

```python
# In pytest
from app.core.permissions import Permission

async def test_update_tag_with_permission(client, test_user):
    """User with edittag permission can update tags."""
    # Give user the permission
    db.add(UserPerms(user_id=test_user.user_id, perm_id=2, permvalue=1))  # edittag
    await db.commit()

    response = await client.put(f"/tags/{tag_id}", json={"name": "New Name"})
    assert response.status_code == 200

async def test_update_tag_without_permission(client, test_user):
    """User without edittag permission gets 403."""
    response = await client.put(f"/tags/{tag_id}", json={"name": "New Name"})
    assert response.status_code == 403
    assert "edittag" in response.json()["detail"]
```

## Common Patterns Summary

| Old Pattern | New Pattern | Use Case |
|-------------|-------------|----------|
| `if not user.admin: raise 403` | `_: RequireAdminLevel` | Admin-only route |
| `if not user.admin: raise 403` | `_: Depends(require_permission(Permission.X))` | Specific permission needed |
| `if own or admin: ...` | `if own or await has_permission(...): ...` | Ownership + elevated permission |
| `if user.admin: ...` | `if await has_permission(..., Permission.ADMIN_LEVEL): ...` | Conditional admin logic |

## Deprecation Timeline

1. **Phase 1** (Current): Permission system available, coexists with `user.admin`
2. **Phase 2**: Migrate all routes to use permissions
3. **Phase 3**: Remove `user.admin` field and `require_admin()` function

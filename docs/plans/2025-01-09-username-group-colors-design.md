# Username Group Colors Design

**Date:** 2025-01-09
**Status:** Approved

## Overview

Enable frontend to display colored usernames based on group membership (e.g., mods get red, admins get green). Backend exposes group names; frontend handles color mapping.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Color definition | Frontend-only | Presentation logic belongs in frontend; can change colors without API changes |
| Where to display | Everywhere usernames appear | Consistency - users expect same styling in comments, images, profiles |
| Data structure | `groups: list[str]` | Simple string array; group names are unique identifiers |
| Priority/ordering | Frontend-managed | Small known group list; frontend config can include priority alongside colors |
| Query strategy | Batch fetch | Most users have no groups; single indexed query is fast for empty results |

## Schema Changes

### UserSummary (app/schemas/user.py)

Add `groups` field to the existing schema:

```python
class UserSummary(BaseModel):
    user_id: int
    username: str
    avatar: str | None = None
    groups: list[str] = []  # NEW: group names like ["mods", "admins"]

    @computed_field
    @property
    def avatar_url(self) -> str | None:
        # existing logic unchanged
```

No model changes needed - existing `Groups` and `UserGroups` tables have all required data.

## Implementation

### New Helper Function

Create batch fetch function (in `app/services/user.py` or new file):

```python
async def get_groups_for_users(db: AsyncSession, user_ids: list[int]) -> dict[int, list[str]]:
    """Fetch group names for multiple users in a single query.

    Returns dict mapping user_id to list of group names.
    Users with no groups will not appear in the result (caller uses .get(id, [])).
    """
    if not user_ids:
        return {}

    query = (
        select(UserGroups.user_id, Groups.title)
        .join(Groups, UserGroups.group_id == Groups.group_id)
        .where(UserGroups.user_id.in_(user_ids))
    )
    result = await db.execute(query)

    groups_by_user: dict[int, list[str]] = {}
    for user_id, group_title in result.fetchall():
        groups_by_user.setdefault(user_id, []).append(group_title)

    return groups_by_user
```

### Integration Pattern

For endpoints returning embedded user info:

```python
# 1. Fetch main data
images = await get_images(db, filters)

# 2. Extract unique user IDs
user_ids = {img.user_id for img in images}

# 3. Batch fetch groups
groups_by_user = await get_groups_for_users(db, user_ids)

# 4. Build response with groups
UserSummary(
    user_id=image.user_id,
    username=image.username,
    avatar=image.avatar,
    groups=groups_by_user.get(image.user_id, [])
)
```

### Affected Endpoints

- `GET /images` - list images with uploaders
- `GET /images/{id}` - single image with uploader
- `GET /images/{id}/comments` - comments with commenters
- Any other endpoints returning embedded `UserSummary`

## Testing

### Unit Tests

Test `get_groups_for_users()`:
- User with no groups → not in result dict
- User with one group → `["mods"]`
- User with multiple groups → `["mods", "admins"]`
- Mixed users (some with groups, some without)
- Empty user_ids list → empty dict

### API Integration Tests

- Image response includes uploader's groups
- Comment response includes commenter's groups
- User with no groups returns `groups: []`

## Edge Cases

| Case | Handling |
|------|----------|
| User with no groups | Empty array `[]` (default) |
| Deleted user in UserSummary | Groups query returns empty |
| Group membership change mid-request | Not a concern; fresh fetch each request |

## Future Considerations

- **Per-user custom colors**: If needed later, could add `custom_color` field to Users model. Frontend would check custom color first, fall back to group-based color.
- **Redis caching**: If group queries become a bottleneck, can layer Redis cache with invalidation on membership changes. Current batch approach should be sufficient.

## Frontend Contract

Backend returns:
```json
{
  "user_id": 123,
  "username": "example",
  "avatar": "abc123.jpg",
  "avatar_url": "https://...",
  "groups": ["mods", "admins"]
}
```

Frontend responsibilities:
- Define group → color mapping
- Define priority when user has multiple groups
- Apply styling consistently across all username displays

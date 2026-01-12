# User Search by Username - Design Document

**Date:** 2025-12-07
**Status:** Approved

## Overview

Add username search functionality to the `GET /api/v1/users/` endpoint to allow partial username matching.

## Requirements

- Add optional `search` query parameter
- Partial username matches should return matching users
- Case-insensitive search
- Minimal changes to existing code

## API Changes

### Endpoint: `GET /api/v1/users/`

**New Query Parameter:**
```
search (optional): Search users by username (partial, case-insensitive match)
```

**Examples:**
- `GET /api/v1/users/?search=john` - Returns all users with "john" in username (John, johnny, JohnDoe, etc.)
- `GET /api/v1/users/?search=alice&page=1&per_page=20` - Search with pagination
- `GET /api/v1/users/` - No search, returns all users (existing behavior)

## Implementation Details

### Code Changes

**File:** `app/api/v1/users.py`

**Function signature update:**
```python
async def list_users(
    pagination: Annotated[PaginationParams, Depends()],
    sorting: Annotated[UserSortParams, Depends()],
    search: Annotated[str | None, Query(description="Search users by username")] = None,
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
```

**Query filter (add after `query = select(Users)`):**
```python
# Apply search filter
if search:
    query = query.where(Users.username.like(f"%{search}%"))
```

**Pattern:** Follows the exact pattern used in `app/api/v1/tags.py:104-105`

**Order of operations:**
1. Build base query
2. Apply search filter (if provided)
3. Count total (with filter applied)
4. Apply sorting
5. Apply pagination
6. Execute query

### Database Behavior

- Uses SQL `LIKE '%search%'` pattern
- MariaDB/MySQL LIKE is case-insensitive by default (based on collation)
- Username column has unique index (app/models/user.py:88), which helps performance
- Wildcards on both sides means full table scan for unindexed searches (acceptable for user listing)

## Testing Strategy

**Test file:** `tests/api/v1/test_users.py`

**Test cases:**

1. **test_list_users_search_matches**
   - Create users: "Alice", "Bob", "AliceWonderland"
   - Search: "alice"
   - Expected: Returns Alice and AliceWonderland, not Bob

2. **test_list_users_search_case_insensitive**
   - Create user: "JohnDoe"
   - Search: "john", "JOHN", "JoHn"
   - Expected: All return JohnDoe

3. **test_list_users_search_no_matches**
   - Search: "nonexistent"
   - Expected: Empty results, total=0

4. **test_list_users_search_empty_returns_all**
   - Search: None or ""
   - Expected: Returns all users

5. **test_list_users_search_with_pagination**
   - Create 25 users matching search
   - Search with per_page=20
   - Expected: Correct total, correct page results

6. **test_list_users_search_special_characters**
   - Create users: "user_name", "user.name", "user-name"
   - Search: "user"
   - Expected: Returns all three

**Edge cases:**
- Very long search strings (should not error)
- Search strings with SQL special characters (%, _) - LIKE wildcards, but acceptable for this use case
- Empty database (total=0, no errors)

## Non-Goals

- Sorting functionality improvements (exists but currently unused, not part of this change)
- Search by other fields (email, location, etc.)
- Advanced search operators (AND, OR, NOT)
- Search performance optimization (indexing strategies)
- SQL injection prevention beyond basic parameterization (SQLAlchemy handles this)

## Implementation Approach (TDD)

1. Write failing tests for all test cases above
2. Run tests to confirm they fail
3. Implement the search parameter and filter
4. Run tests to confirm they pass
5. Manual testing with curl/API client
6. Commit changes

## Security Considerations

- SQLAlchemy parameterizes queries automatically, preventing SQL injection
- No additional sanitization needed for LIKE patterns in this context
- Search does not expose sensitive user data (only public UserResponse fields)

## Rollout

- No migration needed (no database schema changes)
- Backward compatible (search parameter is optional)
- No feature flag needed (simple addition)

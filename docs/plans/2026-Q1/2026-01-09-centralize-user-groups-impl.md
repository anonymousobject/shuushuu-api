# Centralize User Groups Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Centralize user group loading in the Users model so UserSummary automatically includes groups without manual fetching in each endpoint.

**Architecture:** Add SQLAlchemy relationships to Users and UserGroups models, add a `groups` property to Users that returns group names. Update all user-loading queries to eager load groups. UserSummary's `from_attributes=True` will automatically pick up the groups property.

**Tech Stack:** SQLModel/SQLAlchemy, Pydantic v2, pytest

---

## Task 1: Add relationship to UserGroups → Groups

**Files:**
- Modify: `app/models/permissions.py`

**Step 1: Write the failing test**

Create `tests/unit/test_user_groups_relationship.py`:

```python
"""Tests for UserGroups relationship to Groups."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.permissions import Groups, UserGroups


@pytest.mark.asyncio
async def test_user_groups_has_group_relationship(db_session: AsyncSession):
    """UserGroups should have a group relationship that loads the Group."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Create user-group link
    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)
    await db_session.commit()

    # Query with eager loading
    from sqlalchemy import select
    result = await db_session.execute(
        select(UserGroups)
        .options(selectinload(UserGroups.group))
        .where(UserGroups.user_id == 1)
    )
    ug = result.scalar_one()

    assert ug.group is not None
    assert ug.group.title == "mods"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_user_groups_relationship.py -v`
Expected: FAIL with AttributeError (no 'group' attribute)

**Step 3: Write minimal implementation**

Edit `app/models/permissions.py`, add to UserGroups class:

```python
from sqlalchemy.orm import Mapped, relationship

class UserGroups(UserGroupBase, table=True):
    """Database table linking users to groups."""

    __tablename__ = "user_groups"

    # Relationship to Groups for eager loading
    group: Mapped["Groups"] = relationship("Groups", lazy="raise")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_user_groups_relationship.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/models/permissions.py tests/unit/test_user_groups_relationship.py
git commit -m "feat: add group relationship to UserGroups model"
```

---

## Task 2: Add user_groups relationship and groups property to Users

**Files:**
- Modify: `app/models/user.py`
- Add to: `tests/unit/test_user_groups_relationship.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_user_groups_relationship.py`:

```python
from app.models.user import Users


@pytest.mark.asyncio
async def test_users_has_groups_property(db_session: AsyncSession):
    """Users should have a groups property returning group names."""
    from sqlalchemy import select

    # Create groups
    mods = Groups(title="mods", desc="Moderators")
    admins = Groups(title="admins", desc="Administrators")
    db_session.add(mods)
    db_session.add(admins)
    await db_session.flush()

    # Add user 1 to both groups
    db_session.add(UserGroups(user_id=1, group_id=mods.group_id))
    db_session.add(UserGroups(user_id=1, group_id=admins.group_id))
    await db_session.commit()

    # Query user with eager loading
    result = await db_session.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)
        )
        .where(Users.user_id == 1)
    )
    user = result.scalar_one()

    # Check groups property
    assert hasattr(user, "groups")
    assert sorted(user.groups) == ["admins", "mods"]


@pytest.mark.asyncio
async def test_users_groups_property_empty(db_session: AsyncSession):
    """Users with no groups should return empty list."""
    from sqlalchemy import select

    # User 1 exists from fixture but has no groups
    result = await db_session.execute(
        select(Users)
        .options(
            selectinload(Users.user_groups).selectinload(UserGroups.group)
        )
        .where(Users.user_id == 1)
    )
    user = result.scalar_one()

    assert user.groups == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_user_groups_relationship.py::test_users_has_groups_property -v`
Expected: FAIL with AttributeError (no 'user_groups' or 'groups' attribute)

**Step 3: Write minimal implementation**

Edit `app/models/user.py`, add to Users class:

```python
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

if TYPE_CHECKING:
    from app.models.permissions import UserGroups

class Users(UserBase, table=True):
    # ... existing fields ...

    # Relationship to UserGroups for eager loading groups
    user_groups: Mapped[list["UserGroups"]] = relationship(
        "UserGroups",
        primaryjoin="Users.user_id == UserGroups.user_id",
        foreign_keys="UserGroups.user_id",
        lazy="raise",  # Prevent accidental lazy loading in async
    )

    @property
    def groups(self) -> list[str]:
        """
        Get list of group names for this user.

        Requires user_groups relationship to be eager loaded:
            selectinload(Users.user_groups).selectinload(UserGroups.group)

        Returns empty list if user_groups not loaded or user has no groups.
        """
        if not hasattr(self, "_sa_instance_state"):
            return []
        # Check if relationship is loaded to avoid lazy load error
        if "user_groups" not in self.__dict__:
            return []
        return [ug.group.title for ug in self.user_groups if ug.group and ug.group.title]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_user_groups_relationship.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/models/user.py tests/unit/test_user_groups_relationship.py
git commit -m "feat: add user_groups relationship and groups property to Users"
```

---

## Task 3: Create helper for eager loading users with groups

**Files:**
- Create: `app/core/user_loader.py`
- Create: `tests/unit/test_user_loader.py`

**Step 1: Write the failing test**

Create `tests/unit/test_user_loader.py`:

```python
"""Tests for user loader utilities."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_loader import USER_WITH_GROUPS_OPTIONS
from app.models.permissions import Groups, UserGroups
from app.models.user import Users


@pytest.mark.asyncio
async def test_user_with_groups_options(db_session: AsyncSession):
    """USER_WITH_GROUPS_OPTIONS should load users with groups."""
    from sqlalchemy import select

    # Create a group and add user 1 to it
    group = Groups(title="testers", desc="Testers")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    # Query with the standard options
    result = await db_session.execute(
        select(Users).options(*USER_WITH_GROUPS_OPTIONS).where(Users.user_id == 1)
    )
    user = result.scalar_one()

    assert user.groups == ["testers"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_user_loader.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Create `app/core/user_loader.py`:

```python
"""
User loading utilities with standard eager loading options.

Use USER_WITH_GROUPS_OPTIONS when loading users that need groups populated.
This ensures UserSummary.model_validate(user) automatically includes groups.
"""

from sqlalchemy.orm import selectinload

from app.models.permissions import UserGroups
from app.models.user import Users


# Standard options for loading users with their groups
# Usage: select(Users).options(*USER_WITH_GROUPS_OPTIONS)
USER_WITH_GROUPS_OPTIONS = (
    selectinload(Users.user_groups).selectinload(UserGroups.group),
)


def user_with_groups_options():
    """
    Return SQLAlchemy options for loading a user with their groups.

    Usage for loading via relationship:
        selectinload(Images.user).options(*user_with_groups_options())

    Returns options that eager load user_groups and their associated groups,
    so the User.groups property works without additional queries.
    """
    return (
        selectinload(Users.user_groups).selectinload(UserGroups.group),
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_user_loader.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/core/user_loader.py tests/unit/test_user_loader.py
git commit -m "feat: add user loader utilities for eager loading groups"
```

---

## Task 4: Verify UserSummary auto-populates groups from Users.groups property

**Files:**
- Add to: `tests/unit/test_user_loader.py`

**Step 1: Write the test**

Add to `tests/unit/test_user_loader.py`:

```python
from app.schemas.common import UserSummary


@pytest.mark.asyncio
async def test_user_summary_auto_populates_groups(db_session: AsyncSession):
    """UserSummary.model_validate should auto-populate groups from User.groups property."""
    from sqlalchemy import select

    # Create a group and add user 1 to it
    group = Groups(title="auto_test", desc="Auto test group")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    # Query with eager loading
    result = await db_session.execute(
        select(Users).options(*USER_WITH_GROUPS_OPTIONS).where(Users.user_id == 1)
    )
    user = result.scalar_one()

    # Create UserSummary - should auto-populate groups
    summary = UserSummary.model_validate(user)

    assert summary.groups == ["auto_test"]


@pytest.mark.asyncio
async def test_user_summary_empty_groups_when_not_loaded(db_session: AsyncSession):
    """UserSummary should have empty groups when user_groups not eager loaded."""
    from sqlalchemy import select

    # Query WITHOUT eager loading
    result = await db_session.execute(
        select(Users).where(Users.user_id == 1)
    )
    user = result.scalar_one()

    # Create UserSummary - should have empty groups (not raise error)
    summary = UserSummary.model_validate(user)

    assert summary.groups == []
```

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_user_loader.py -v`
Expected: PASS (this validates the integration works)

**Step 3: Commit**

```bash
git add tests/unit/test_user_loader.py
git commit -m "test: verify UserSummary auto-populates groups from User property"
```

---

## Task 5: Update Images.user relationship to eager load groups

**Files:**
- Modify: `app/models/image.py`
- Modify: `app/api/v1/images.py`

**Step 1: Write the failing test**

Add to `tests/api/v1/test_images_groups.py`:

```python
@pytest.mark.asyncio
async def test_list_images_groups_via_relationship(
    client: AsyncClient, db_session: AsyncSession
):
    """Images should include groups via the eager-loaded User.groups property."""
    # Create a group and add user 1 to it
    group = Groups(title="relationship_test", desc="Relationship Test")
    db_session.add(group)
    await db_session.flush()
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))

    # Create an image
    image = Images(
        filename="test-relationship-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="reltest001hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()

    response = await client.get("/api/v1/images")
    assert response.status_code == 200

    data = response.json()
    test_image = next(
        (img for img in data["images"] if img["filename"] == "test-relationship-001"),
        None,
    )
    assert test_image is not None
    assert test_image["user"]["groups"] == ["relationship_test"]
```

**Step 2: Run test - should pass with current implementation**

Run: `uv run pytest tests/api/v1/test_images_groups.py::test_list_images_groups_via_relationship -v`

**Step 3: Update image loading to use eager load**

Edit `app/api/v1/images.py`. Replace the user selectinload pattern:

From:
```python
selectinload(Images.user).load_only(Users.user_id, Users.username, Users.avatar)
```

To:
```python
from app.core.user_loader import user_with_groups_options

selectinload(Images.user).load_only(
    Users.user_id, Users.username, Users.avatar
).options(*user_with_groups_options())
```

Note: Actually, `load_only` and nested `selectinload` don't combine well. We need a different approach - load the full user but use `selectinload` for the nested groups:

```python
selectinload(Images.user).selectinload(Users.user_groups).selectinload(UserGroups.group)
```

**Step 4: Remove manual groups fetching**

In `app/api/v1/images.py`, remove:
- The `get_groups_for_users` import
- The `user_ids = {img.user_id...}` and `groups_by_user = await get_groups_for_users(...)` lines
- The `groups_by_user=groups_by_user` parameter in `from_db_model` calls

Update `ImageDetailedResponse.from_db_model` to not need `groups_by_user` since UserSummary will auto-populate.

**Step 5: Run tests**

Run: `uv run pytest tests/api/v1/test_images_groups.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add app/api/v1/images.py app/schemas/image.py
git commit -m "refactor: use eager-loaded User.groups in images endpoint"
```

---

## Task 6: Update Comments to use eager-loaded groups

**Files:**
- Modify: `app/api/v1/comments.py`
- Modify: `app/schemas/comment.py`

**Step 1: Update comment loading**

Edit `app/api/v1/comments.py`. Replace the user selectinload pattern in all comment queries:

From:
```python
selectinload(Comments.user).load_only(Users.user_id, Users.username, Users.avatar)
```

To:
```python
from app.models.permissions import UserGroups

selectinload(Comments.user).selectinload(Users.user_groups).selectinload(UserGroups.group)
```

**Step 2: Simplify build_comment_response**

Since UserSummary now auto-populates groups, simplify or remove `build_comment_response`:

```python
def build_comment_response(comment: Any) -> CommentResponse:
    """Build CommentResponse from database model."""
    return CommentResponse.model_validate(comment)
```

Or just use `CommentResponse.model_validate(comment)` directly again.

**Step 3: Remove manual groups fetching**

Remove:
- The `get_groups_for_users` import
- The `user_ids` and `groups_by_user` lines
- The `groups_by_user` parameters

**Step 4: Run tests**

Run: `uv run pytest tests/api/v1/test_comments_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/comments.py app/schemas/comment.py
git commit -m "refactor: use eager-loaded User.groups in comments endpoint"
```

---

## Task 7: Update Privmsgs to include groups

**Files:**
- Modify: `app/schemas/privmsg.py`
- Modify: `app/api/v1/privmsgs.py`

**Step 1: Update PrivmsgMessage schema**

Add groups fields to the schema:

```python
class PrivmsgMessage(PrivmsgBase):
    """Schema for retrieving private messages for a user"""

    privmsg_id: int
    viewed: int
    from_username: str | None = None
    to_username: str | None = None
    from_avatar_url: str | None = None
    to_avatar_url: str | None = None
    from_groups: list[str] = []  # NEW
    to_groups: list[str] = []    # NEW
    # ... rest unchanged
```

**Step 2: Update privmsgs endpoint**

The privmsgs endpoint currently does manual joins for username/avatar. We need to also fetch groups. Update to use eager loading with the Users model.

**Step 3: Write test**

Create `tests/api/v1/test_privmsgs_groups.py` with tests for groups in privmsg responses.

**Step 4: Commit**

```bash
git add app/schemas/privmsg.py app/api/v1/privmsgs.py tests/api/v1/test_privmsgs_groups.py
git commit -m "feat: include user groups in privmsg responses"
```

---

## Task 8: Update Tags to use UserSummary for created_by

**Files:**
- Modify: `app/schemas/tag.py`
- Modify: `app/api/v1/tags.py`

**Step 1: Replace TagCreator with UserSummary**

`TagCreator` is essentially a duplicate of `UserSummary`. Replace it:

```python
# In app/schemas/tag.py
from app.schemas.common import UserSummary

class TagWithStats(TagResponse):
    # ...
    created_by: UserSummary | None = None  # Changed from TagCreator
```

**Step 2: Update tags endpoint**

Update the tags endpoint to eager load user groups when fetching created_by user.

**Step 3: Remove TagCreator class**

Remove the redundant `TagCreator` class from `app/schemas/tag.py`.

**Step 4: Commit**

```bash
git add app/schemas/tag.py app/api/v1/tags.py
git commit -m "refactor: use UserSummary for tag created_by with groups"
```

---

## Task 9: Update remaining endpoints (users, favorites)

**Files:**
- Audit: `app/api/v1/users.py`
- Audit: `app/api/v1/favorites.py`

**Step 1: Check users endpoint**

The `/users/` list endpoint returns `UserResponse` which may need groups. Check if users listing needs groups (probably yes for user search results).

**Step 2: Check favorites endpoint**

The favorites endpoint embeds user info in image responses. Ensure it uses eager loading.

**Step 3: Update as needed and commit**

---

## Task 10: Clean up old code

**Files:**
- Delete or simplify: `app/services/user_groups.py`
- Remove old tests if superseded

**Step 1: Check if user_groups service is still needed**

The `get_groups_for_users` function may no longer be needed if all endpoints use eager loading. However, keep it if there are edge cases where batch fetching is still useful.

**Step 2: Clean up**

Remove unused imports and code across the codebase.

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: clean up unused groups fetching code"
```

---

## Task 11: Run full test suite and fix regressions

**Step 1: Run all tests**

Run: `uv run pytest -v`

**Step 2: Fix any failures**

**Step 3: Run linter**

Run: `uv run ruff check app/ tests/`

**Step 4: Commit fixes**

```bash
git add -A
git commit -m "fix: resolve test regressions from groups refactor"
```

---

## Task 12: Final verification

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Manual API verification**

Test key endpoints manually to verify groups appear correctly:
- `GET /api/v1/images`
- `GET /api/v1/images/{id}`
- `GET /api/v1/comments`
- `GET /api/v1/privmsgs/received`
- `GET /api/v1/tags/{id}`

**Step 3: Commit any final changes**

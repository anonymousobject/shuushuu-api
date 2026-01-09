# Username Group Colors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose user group memberships in API responses so frontend can display colored usernames based on group (e.g., mods = red, admins = green).

**Architecture:** Add `groups: list[str]` to `UserSummary` schema. Create batch-fetch helper `get_groups_for_users()` in new service module. Modify image and comment endpoints to fetch groups and pass them when building responses.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2, pytest

---

## Task 1: Add groups field to UserSummary schema

**Files:**
- Modify: `app/schemas/common.py:10-32`

**Step 1: Write the failing test**

Create test file `tests/unit/test_user_summary_groups.py`:

```python
"""Tests for UserSummary groups field."""

import pytest
from app.schemas.common import UserSummary


def test_user_summary_groups_default_empty():
    """UserSummary should have empty groups list by default."""
    summary = UserSummary(user_id=1, username="testuser")
    assert summary.groups == []


def test_user_summary_groups_with_values():
    """UserSummary should accept groups list."""
    summary = UserSummary(user_id=1, username="testuser", groups=["mods", "admins"])
    assert summary.groups == ["mods", "admins"]


def test_user_summary_groups_in_json():
    """UserSummary groups should appear in JSON output."""
    summary = UserSummary(user_id=1, username="testuser", groups=["mods"])
    data = summary.model_dump()
    assert "groups" in data
    assert data["groups"] == ["mods"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_user_summary_groups.py -v`
Expected: FAIL with validation error (groups field doesn't exist)

**Step 3: Write minimal implementation**

Edit `app/schemas/common.py`:

```python
class UserSummary(BaseModel):
    """
    Minimal user information for embedding in responses.

    Used across image, comment, and other endpoints to avoid N+1 queries
    when clients need basic user info without fetching the full user profile.
    """

    user_id: int
    username: str
    avatar: str | None = None  # Avatar filename from database
    groups: list[str] = []  # Group names for username coloring (e.g., ["mods", "admins"])

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        if self.avatar:
            return f"{settings.IMAGE_BASE_URL}/images/avatars/{self.avatar}"
        return None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_user_summary_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/common.py tests/unit/test_user_summary_groups.py
git commit -m "feat: add groups field to UserSummary schema"
```

---

## Task 2: Create get_groups_for_users service function

**Files:**
- Create: `app/services/user_groups.py`
- Create: `tests/unit/test_user_groups_service.py`

**Step 1: Write the failing test**

Create test file `tests/unit/test_user_groups_service.py`:

```python
"""Tests for user groups service."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import Groups, UserGroups
from app.services.user_groups import get_groups_for_users


@pytest.mark.asyncio
async def test_get_groups_for_users_empty_list(db_session: AsyncSession):
    """Empty user_ids list returns empty dict."""
    result = await get_groups_for_users(db_session, [])
    assert result == {}


@pytest.mark.asyncio
async def test_get_groups_for_users_no_groups(db_session: AsyncSession):
    """Users with no groups don't appear in result."""
    # User 1 exists from db_session fixture but has no groups
    result = await get_groups_for_users(db_session, [1])
    assert result == {}


@pytest.mark.asyncio
async def test_get_groups_for_users_with_groups(db_session: AsyncSession):
    """Users with groups return their group names."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Add user 1 to the group
    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)
    await db_session.commit()

    result = await get_groups_for_users(db_session, [1])
    assert result == {1: ["mods"]}


@pytest.mark.asyncio
async def test_get_groups_for_users_multiple_groups(db_session: AsyncSession):
    """User with multiple groups returns all group names."""
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

    result = await get_groups_for_users(db_session, [1])
    assert 1 in result
    assert sorted(result[1]) == ["admins", "mods"]


@pytest.mark.asyncio
async def test_get_groups_for_users_mixed(db_session: AsyncSession):
    """Mix of users with and without groups."""
    # Create a group
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    # Only user 1 gets the group, user 2 has no groups
    db_session.add(UserGroups(user_id=1, group_id=group.group_id))
    await db_session.commit()

    result = await get_groups_for_users(db_session, [1, 2])
    assert result == {1: ["mods"]}
    assert 2 not in result  # User with no groups not in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_user_groups_service.py -v`
Expected: FAIL with "No module named 'app.services.user_groups'"

**Step 3: Write minimal implementation**

Create `app/services/user_groups.py`:

```python
"""User groups service for fetching group memberships."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permissions import Groups, UserGroups


async def get_groups_for_users(
    db: AsyncSession, user_ids: list[int]
) -> dict[int, list[str]]:
    """
    Fetch group names for multiple users in a single query.

    Args:
        db: Database session
        user_ids: List of user IDs to fetch groups for

    Returns:
        Dict mapping user_id to list of group names.
        Users with no groups will not appear in the result.
        Caller should use .get(user_id, []) to handle missing users.
    """
    if not user_ids:
        return {}

    query = (
        select(UserGroups.user_id, Groups.title)
        .join(Groups, UserGroups.group_id == Groups.group_id)
        .where(UserGroups.user_id.in_(user_ids))  # type: ignore[union-attr]
    )
    result = await db.execute(query)

    groups_by_user: dict[int, list[str]] = {}
    for user_id, group_title in result.fetchall():
        if group_title:  # Skip null titles
            groups_by_user.setdefault(user_id, []).append(group_title)

    return groups_by_user
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_user_groups_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/user_groups.py tests/unit/test_user_groups_service.py
git commit -m "feat: add get_groups_for_users service function"
```

---

## Task 3: Update ImageDetailedResponse.from_db_model to accept groups

**Files:**
- Modify: `app/schemas/image.py:149-181`
- Create: `tests/unit/test_image_response_groups.py`

**Step 1: Write the failing test**

Create test file `tests/unit/test_image_response_groups.py`:

```python
"""Tests for ImageDetailedResponse groups support."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime

from app.schemas.image import ImageDetailedResponse


def _create_mock_image(user_id: int = 1, username: str = "testuser"):
    """Create a mock image object for testing."""
    mock_image = MagicMock()
    mock_image.image_id = 1
    mock_image.filename = "test"
    mock_image.ext = "jpg"
    mock_image.original_filename = "test.jpg"
    mock_image.md5_hash = "abc123"
    mock_image.filesize = 1000
    mock_image.width = 100
    mock_image.height = 100
    mock_image.caption = "Test"
    mock_image.rating = 0.0
    mock_image.user_id = user_id
    mock_image.date_added = datetime.now()
    mock_image.status = 1
    mock_image.locked = 0
    mock_image.posts = 0
    mock_image.favorites = 0
    mock_image.bayesian_rating = 0.0
    mock_image.num_ratings = 0
    mock_image.medium = 0
    mock_image.large = 0
    mock_image.replacement_id = None

    # Mock user relationship
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_image.user = mock_user

    # No tags
    mock_image.tag_links = []

    return mock_image


def test_from_db_model_without_groups():
    """from_db_model without groups_by_user uses empty groups."""
    mock_image = _create_mock_image()
    response = ImageDetailedResponse.from_db_model(mock_image)
    assert response.user is not None
    assert response.user.groups == []


def test_from_db_model_with_groups():
    """from_db_model with groups_by_user populates user groups."""
    mock_image = _create_mock_image(user_id=1)
    groups_by_user = {1: ["mods", "admins"]}
    response = ImageDetailedResponse.from_db_model(mock_image, groups_by_user=groups_by_user)
    assert response.user is not None
    assert response.user.groups == ["mods", "admins"]


def test_from_db_model_user_not_in_groups_dict():
    """from_db_model with groups_by_user but user not in dict gets empty groups."""
    mock_image = _create_mock_image(user_id=99)
    groups_by_user = {1: ["mods"]}  # User 99 not in dict
    response = ImageDetailedResponse.from_db_model(mock_image, groups_by_user=groups_by_user)
    assert response.user is not None
    assert response.user.groups == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_image_response_groups.py -v`
Expected: FAIL with "got an unexpected keyword argument 'groups_by_user'"

**Step 3: Write minimal implementation**

Edit `app/schemas/image.py`, modify the `from_db_model` method:

```python
@classmethod
def from_db_model(
    cls,
    image: Any,
    is_favorited: bool = False,
    user_rating: int | None = None,
    prev_image_id: int | None = None,
    next_image_id: int | None = None,
    groups_by_user: dict[int, list[str]] | None = None,
) -> "ImageDetailedResponse":
    """Create response from database model with relationships"""
    data = ImageResponse.model_validate(image).model_dump()

    # Add user if loaded
    if hasattr(image, "user") and image.user:
        user_groups = []
        if groups_by_user:
            user_groups = groups_by_user.get(image.user.user_id, [])
        data["user"] = UserSummary(
            user_id=image.user.user_id,
            username=image.user.username,
            avatar=image.user.avatar,
            groups=user_groups,
        )

    # Add tags if loaded through tag_links, sorted by type then alphabetically
    if hasattr(image, "tag_links") and image.tag_links:
        sorted_links = sorted(
            image.tag_links,
            key=lambda tl: (
                TAG_TYPE_SORT_ORDER.get(tl.tag.type, 99),  # Primary: type order
                (tl.tag.title or "").lower(),  # Secondary: alphabetical
            ),
        )
        data["tags"] = [TagSummary.model_validate(tl.tag) for tl in sorted_links]

    data["is_favorited"] = is_favorited
    data["user_rating"] = user_rating
    data["prev_image_id"] = prev_image_id
    data["next_image_id"] = next_image_id

    return cls(**data)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_image_response_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/image.py tests/unit/test_image_response_groups.py
git commit -m "feat: add groups_by_user param to ImageDetailedResponse.from_db_model"
```

---

## Task 4: Integrate groups into list_images endpoint

**Files:**
- Modify: `app/api/v1/images.py:88-387`
- Create: `tests/api/v1/test_images_groups.py`

**Step 1: Write the failing test**

Create test file `tests/api/v1/test_images_groups.py`:

```python
"""Tests for groups in image API responses."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.models.permissions import Groups, UserGroups


@pytest.mark.asyncio
async def test_list_images_includes_empty_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Images endpoint returns empty groups array for users without groups."""
    # Create an image (user 1 exists from fixture, has no groups)
    image = Images(
        filename="test-groups-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="groups001hash",
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
    assert len(data["images"]) >= 1

    # Find our image
    test_image = next(
        (img for img in data["images"] if img["filename"] == "test-groups-001"), None
    )
    assert test_image is not None
    assert test_image["user"]["groups"] == []


@pytest.mark.asyncio
async def test_list_images_includes_user_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Images endpoint returns user's groups in response."""
    # Create a group and add user 1 to it
    group = Groups(title="mods", desc="Moderators")
    db_session.add(group)
    await db_session.flush()

    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)

    # Create an image
    image = Images(
        filename="test-groups-002",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="groups002hash",
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
        (img for img in data["images"] if img["filename"] == "test-groups-002"), None
    )
    assert test_image is not None
    assert test_image["user"]["groups"] == ["mods"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images_groups.py -v`
Expected: FAIL - groups key missing or wrong value

**Step 3: Write minimal implementation**

Edit `app/api/v1/images.py`:

1. Add import at top:
```python
from app.services.user_groups import get_groups_for_users
```

2. In `list_images` function, after fetching images and before building response (around line 377), add:

```python
    # Execute query
    result = await db.execute(final_query)
    images = result.scalars().all()

    # Fetch groups for all users in the result set
    user_ids = {img.user_id for img in images if img.user_id}
    groups_by_user = await get_groups_for_users(db, list(user_ids))

    # Get favorite status for authenticated users (separate query for clean separation)
    # ... existing code ...

    return ImageDetailedListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        images=[
            ImageDetailedResponse.from_db_model(
                img,
                is_favorited=img.image_id in favorited_ids,
                groups_by_user=groups_by_user,
            )
            for img in images
        ],
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images_groups.py
git commit -m "feat: include user groups in list_images response"
```

---

## Task 5: Integrate groups into get_image endpoint

**Files:**
- Modify: `app/api/v1/images.py:390-478`

**Step 1: Write the failing test**

Add to `tests/api/v1/test_images_groups.py`:

```python
@pytest.mark.asyncio
async def test_get_image_includes_user_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Single image endpoint returns user's groups."""
    # Create a group and add user 1 to it
    group = Groups(title="testers", desc="Testers")
    db_session.add(group)
    await db_session.flush()

    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)

    # Create an image
    image = Images(
        filename="test-groups-003",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="groups003hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)

    response = await client.get(f"/api/v1/images/{image.image_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["user"]["groups"] == ["testers"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_images_groups.py::test_get_image_includes_user_groups -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `get_image` function, after fetching the image and before building response (around line 470), add:

```python
    # Fetch groups for the image uploader
    groups_by_user = {}
    if image.user_id:
        groups_by_user = await get_groups_for_users(db, [image.user_id])

    return ImageDetailedResponse.from_db_model(
        image,
        is_favorited=is_favorited,
        user_rating=user_rating,
        prev_image_id=prev_image_id,
        next_image_id=next_image_id,
        groups_by_user=groups_by_user,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_images_groups.py::test_get_image_includes_user_groups -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/images.py tests/api/v1/test_images_groups.py
git commit -m "feat: include user groups in get_image response"
```

---

## Task 6: Create helper to build CommentResponse with groups

**Files:**
- Modify: `app/schemas/comment.py`
- Create: `tests/unit/test_comment_response_groups.py`

**Step 1: Write the failing test**

Create `tests/unit/test_comment_response_groups.py`:

```python
"""Tests for CommentResponse groups support."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from app.schemas.comment import CommentResponse, build_comment_response


def _create_mock_comment(user_id: int = 1, username: str = "testuser"):
    """Create a mock comment object for testing."""
    mock_comment = MagicMock()
    mock_comment.post_id = 1
    mock_comment.image_id = 1
    mock_comment.user_id = user_id
    mock_comment.post_text = "Test comment"
    mock_comment.date = datetime.now()
    mock_comment.update_count = 0
    mock_comment.last_updated = None
    mock_comment.last_updated_user_id = None
    mock_comment.parent_comment_id = None
    mock_comment.deleted = False

    # Mock user relationship
    mock_user = MagicMock()
    mock_user.user_id = user_id
    mock_user.username = username
    mock_user.avatar = None
    mock_comment.user = mock_user

    return mock_comment


def test_build_comment_response_without_groups():
    """build_comment_response without groups uses empty list."""
    mock_comment = _create_mock_comment()
    response = build_comment_response(mock_comment)
    assert response.user.groups == []


def test_build_comment_response_with_groups():
    """build_comment_response with groups populates user groups."""
    mock_comment = _create_mock_comment(user_id=1)
    groups_by_user = {1: ["mods"]}
    response = build_comment_response(mock_comment, groups_by_user=groups_by_user)
    assert response.user.groups == ["mods"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_comment_response_groups.py -v`
Expected: FAIL with "cannot import name 'build_comment_response'"

**Step 3: Write minimal implementation**

Add to `app/schemas/comment.py`:

```python
from app.schemas.common import UserSummary

# ... existing code ...

def build_comment_response(
    comment: Any,
    groups_by_user: dict[int, list[str]] | None = None,
) -> CommentResponse:
    """
    Build CommentResponse from database model with optional groups.

    Args:
        comment: Comment database model with user relationship loaded
        groups_by_user: Optional dict mapping user_id to list of group names

    Returns:
        CommentResponse with user groups populated
    """
    # Build base response using model_validate
    response = CommentResponse.model_validate(comment)

    # Override user with groups if available
    if comment.user:
        user_groups = []
        if groups_by_user:
            user_groups = groups_by_user.get(comment.user.user_id, [])
        response.user = UserSummary(
            user_id=comment.user.user_id,
            username=comment.user.username,
            avatar=comment.user.avatar,
            groups=user_groups,
        )

    return response
```

Also add the import at the top:
```python
from typing import Any
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_comment_response_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/schemas/comment.py tests/unit/test_comment_response_groups.py
git commit -m "feat: add build_comment_response helper with groups support"
```

---

## Task 7: Integrate groups into comments endpoints

**Files:**
- Modify: `app/api/v1/comments.py`
- Create: `tests/api/v1/test_comments_groups.py`

**Step 1: Write the failing test**

Create `tests/api/v1/test_comments_groups.py`:

```python
"""Tests for groups in comment API responses."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comments
from app.models.image import Images
from app.models.permissions import Groups, UserGroups


@pytest.mark.asyncio
async def test_list_comments_includes_user_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Comments list endpoint returns user's groups."""
    # Create a group and add user 1 to it
    group = Groups(title="commenters", desc="Commenters")
    db_session.add(group)
    await db_session.flush()

    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)

    # Create an image
    image = Images(
        filename="test-comment-groups-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="cgroups001hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.flush()

    # Create a comment
    comment = Comments(
        image_id=image.image_id,
        user_id=1,
        post_text="Test comment with groups",
    )
    db_session.add(comment)
    await db_session.commit()

    response = await client.get("/api/v1/comments")
    assert response.status_code == 200

    data = response.json()
    assert len(data["comments"]) >= 1

    # Find our comment
    test_comment = next(
        (c for c in data["comments"] if c["post_text"] == "Test comment with groups"),
        None,
    )
    assert test_comment is not None
    assert test_comment["user"]["groups"] == ["commenters"]


@pytest.mark.asyncio
async def test_get_comment_includes_user_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """Single comment endpoint returns user's groups."""
    # Create a group and add user 1 to it
    group = Groups(title="single_commenters", desc="Single Commenters")
    db_session.add(group)
    await db_session.flush()

    user_group = UserGroups(user_id=1, group_id=group.group_id)
    db_session.add(user_group)

    # Create an image
    image = Images(
        filename="test-comment-groups-002",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="cgroups002hash",
        filesize=1000,
        width=100,
        height=100,
        user_id=1,
        status=1,
        locked=0,
    )
    db_session.add(image)
    await db_session.flush()

    # Create a comment
    comment = Comments(
        image_id=image.image_id,
        user_id=1,
        post_text="Single comment with groups",
    )
    db_session.add(comment)
    await db_session.commit()
    await db_session.refresh(comment)

    response = await client.get(f"/api/v1/comments/{comment.post_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["user"]["groups"] == ["single_commenters"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_comments_groups.py -v`
Expected: FAIL - groups missing or wrong value

**Step 3: Write minimal implementation**

Edit `app/api/v1/comments.py`:

1. Add imports at top:
```python
from app.schemas.comment import build_comment_response
from app.services.user_groups import get_groups_for_users
```

2. In `list_comments` (around line 155):
```python
    # Execute query
    result = await db.execute(query)
    comments = result.scalars().all()

    # Fetch groups for all commenters
    user_ids = {c.user_id for c in comments if c.user_id}
    groups_by_user = await get_groups_for_users(db, list(user_ids))

    return CommentListResponse(
        total=total or 0,
        page=pagination.page,
        per_page=pagination.per_page,
        comments=[build_comment_response(comment, groups_by_user) for comment in comments],
    )
```

3. In `get_comment` (around line 184):
```python
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Fetch groups for the commenter
    groups_by_user = {}
    if comment.user_id:
        groups_by_user = await get_groups_for_users(db, [comment.user_id])

    return build_comment_response(comment, groups_by_user)
```

4. Update other comment endpoints similarly: `get_image_comments`, `get_user_comments`, `create_comment`, `update_comment`, `delete_comment`

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_comments_groups.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/v1/comments.py tests/api/v1/test_comments_groups.py
git commit -m "feat: include user groups in comment responses"
```

---

## Task 8: Run full test suite and fix any regressions

**Step 1: Run all tests**

Run: `uv run pytest -v`

**Step 2: Fix any failures**

If tests fail, analyze the failures and fix them. Common issues:
- Tests expecting specific response structure may need updating
- Snapshot tests may need regeneration

**Step 3: Run linter**

Run: `uv run ruff check app/ tests/`

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test regressions from groups feature"
```

---

## Task 9: Final verification and PR

**Step 1: Run full test suite one more time**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Create pull request**

```bash
git push -u origin feature/username-group-colors
gh pr create --title "feat: add user groups to API responses for username coloring" --body "$(cat <<'EOF'
## Summary
- Add `groups: list[str]` field to `UserSummary` schema
- Create `get_groups_for_users()` service for batch fetching
- Integrate groups into image and comment API responses
- Frontend can now map group names to colors for username styling

## Test plan
- [x] Unit tests for UserSummary groups field
- [x] Unit tests for get_groups_for_users service
- [x] API tests for images endpoint with groups
- [x] API tests for comments endpoint with groups
- [x] Full test suite passes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Step 3: Return PR URL**

Output the PR URL for the user.

# Admin Moderation Endpoints Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add direct image moderation, admin comment deletion, and fix user profile edit authorization.

**Architecture:** Extend existing admin.py with image status endpoint, modify comments.py and users.py to use permission system instead of legacy admin flag.

**Tech Stack:** FastAPI, SQLModel, Pydantic schemas, pytest with async fixtures

---

## Task 1: Add AdminActionType Constants

**Files:**
- Modify: `app/config.py:212-221`

**Step 1: Add new action type constants**

In `app/config.py`, add two new constants to `AdminActionType` class:

```python
class AdminActionType:
    """Admin action type constants for audit logging"""

    REPORT_DISMISS = 1
    REPORT_ACTION = 2
    REVIEW_START = 3
    REVIEW_VOTE = 4
    REVIEW_CLOSE = 5
    REVIEW_EXTEND = 6
    IMAGE_STATUS_CHANGE = 7  # ADD THIS
    COMMENT_DELETE = 8       # ADD THIS
```

**Step 2: Commit**

```bash
git add app/config.py
git commit -m "feat: add IMAGE_STATUS_CHANGE and COMMENT_DELETE action types"
```

---

## Task 2: Add Image Status Schemas

**Files:**
- Modify: `app/schemas/admin.py` (add at end)

**Step 1: Add ImageStatusUpdate request schema**

Add to end of `app/schemas/admin.py`:

```python
# ===== Image Status Schemas =====


class ImageStatusUpdate(BaseModel):
    """Request schema for changing image status directly."""

    status: int = Field(
        ...,
        description="New status: -4=Review, -2=Inappropriate, -1=Repost, 0=Other, 1=Active, 2=Spoiler",
    )
    replacement_id: int | None = Field(
        None,
        description="Original image ID when marking as repost (required when status=-1)",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        """Validate status is one of the allowed ImageStatus constants."""
        from app.config import ImageStatus

        valid_statuses = {
            ImageStatus.REVIEW,
            ImageStatus.INAPPROPRIATE,
            ImageStatus.REPOST,
            ImageStatus.OTHER,
            ImageStatus.ACTIVE,
            ImageStatus.SPOILER,
        }
        if v not in valid_statuses:
            raise ValueError(
                f"Invalid status: {v}. Must be one of: "
                "-4=Review, -2=Inappropriate, -1=Repost, 0=Other, 1=Active, 2=Spoiler"
            )
        return v


class ImageStatusResponse(BaseModel):
    """Response schema for image status change."""

    image_id: int
    status: int
    replacement_id: int | None
    status_user_id: int | None
    status_updated: datetime | None

    model_config = {"from_attributes": True}
```

**Step 2: Commit**

```bash
git add app/schemas/admin.py
git commit -m "feat: add ImageStatusUpdate and ImageStatusResponse schemas"
```

---

## Task 3: Add Direct Image Moderation Endpoint - Tests First

**Files:**
- Create: `tests/api/v1/test_admin_images.py`

**Step 1: Write failing tests for image status change**

Create `tests/api/v1/test_admin_images.py`:

```python
"""
Tests for image moderation admin endpoints.

These tests cover the PATCH /api/v1/admin/images/{image_id} endpoint.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def create_admin_user(
    db_session: AsyncSession,
    username: str = "imageadmin",
    email: str = "imageadmin@example.com",
) -> tuple[Users, str]:
    """Create an admin user and return the user object and password."""
    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def create_regular_user(
    db_session: AsyncSession,
    username: str = "regularuser",
    email: str = "regular@example.com",
) -> Users:
    """Create a regular user."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "image_test_admin")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="image_test_admin", desc="Image test admin group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


async def login_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_test_image(db_session: AsyncSession, user_id: int) -> Images:
    """Create a test image."""
    image = Images(
        user_id=user_id,
        filename="test_image",
        ext="jpg",
        md5_hash="abc123def456abc123def456abc12345",
        status=ImageStatus.ACTIVE,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


@pytest.mark.api
class TestImageStatusChange:
    """Tests for PATCH /api/v1/admin/images/{image_id} endpoint."""

    async def test_mark_image_as_spoiler(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test marking an image as spoiler."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.SPOILER},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == ImageStatus.SPOILER
        assert data["status_user_id"] == admin.user_id

    async def test_mark_image_as_repost_with_original(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test marking an image as repost with original image ID."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")

        original_image = await create_test_image(db_session, admin.user_id)
        repost_image = Images(
            user_id=admin.user_id,
            filename="repost_image",
            ext="jpg",
            md5_hash="xyz789xyz789xyz789xyz789xyz78901",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(repost_image)
        await db_session.commit()
        await db_session.refresh(repost_image)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{repost_image.image_id}",
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": original_image.image_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == ImageStatus.REPOST
        assert data["replacement_id"] == original_image.image_id

    async def test_repost_requires_replacement_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that marking as repost without replacement_id fails."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.REPOST},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "replacement_id" in response.json()["detail"].lower()

    async def test_cannot_repost_self(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that an image cannot be marked as a repost of itself."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": image.image_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "itself" in response.json()["detail"].lower()

    async def test_invalid_replacement_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that invalid replacement_id fails."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": 999999,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert "original image" in response.json()["detail"].lower()

    async def test_clears_replacement_id_when_changing_from_repost(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that replacement_id is cleared when changing status away from repost."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")

        original = await create_test_image(db_session, admin.user_id)
        repost = Images(
            user_id=admin.user_id,
            filename="repost",
            ext="jpg",
            md5_hash="clear123clear123clear123clear123",
            status=ImageStatus.REPOST,
            replacement_id=original.image_id,
        )
        db_session.add(repost)
        await db_session.commit()
        await db_session.refresh(repost)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{repost.image_id}",
            json={"status": ImageStatus.ACTIVE},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == ImageStatus.ACTIVE
        assert data["replacement_id"] is None

    async def test_requires_image_edit_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that endpoint requires IMAGE_EDIT permission."""
        user = await create_regular_user(db_session, username="noperm", email="noperm@example.com")
        image = await create_test_image(db_session, user.user_id)

        token = await login_user(client, user.username, "TestPassword123!")

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.SPOILER},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_image_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test 404 for non-existent image."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            "/api/v1/admin/images/999999",
            json={"status": ImageStatus.SPOILER},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_invalid_status_value(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test validation error for invalid status value."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": 99},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_admin_images.py -v`
Expected: FAIL (endpoint does not exist)

**Step 3: Commit test file**

```bash
git add tests/api/v1/test_admin_images.py
git commit -m "test: add tests for PATCH /admin/images/{image_id} endpoint"
```

---

## Task 4: Implement Image Status Change Endpoint

**Files:**
- Modify: `app/api/v1/admin.py` (add endpoint)

**Step 1: Add imports at top of admin.py**

Ensure these imports exist (add if missing):

```python
from app.schemas.admin import (
    # ... existing imports ...
    ImageStatusUpdate,
    ImageStatusResponse,
)
```

**Step 2: Add the endpoint**

Add before the report endpoints section (around line 600):

```python
# ===== Direct Image Moderation =====


@router.patch("/images/{image_id}", response_model=ImageStatusResponse)
async def change_image_status(
    image_id: Annotated[int, Path(description="Image ID")],
    status_data: ImageStatusUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    _: Annotated[None, Depends(require_permission(Permission.IMAGE_EDIT))],
    db: AsyncSession = Depends(get_db),
) -> ImageStatusResponse:
    """
    Change an image's status directly.

    Use this for quick moderation actions without creating a report.

    Requires IMAGE_EDIT permission.
    """
    # Get the image
    result = await db.execute(
        select(Images).where(Images.image_id == image_id)  # type: ignore[arg-type]
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = image.status

    # Handle repost status
    if status_data.status == ImageStatus.REPOST:
        if status_data.replacement_id is None:
            raise HTTPException(
                status_code=400,
                detail="replacement_id is required when marking as repost",
            )
        if status_data.replacement_id == image_id:
            raise HTTPException(
                status_code=400,
                detail="An image cannot be a repost of itself",
            )
        # Verify original image exists
        original_result = await db.execute(
            select(Images).where(Images.image_id == status_data.replacement_id)  # type: ignore[arg-type]
        )
        if not original_result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail="Original image not found",
            )
        image.replacement_id = status_data.replacement_id
    else:
        # Clear replacement_id when not a repost
        image.replacement_id = None

    # Update image status
    image.status = status_data.status
    image.status_user_id = current_user.user_id
    image.status_updated = datetime.now(UTC)

    # Log admin action
    action = AdminActions(
        user_id=current_user.user_id,
        action_type=AdminActionType.IMAGE_STATUS_CHANGE,
        image_id=image_id,
        details={
            "previous_status": previous_status,
            "new_status": status_data.status,
            "replacement_id": status_data.replacement_id,
        },
    )
    db.add(action)

    await db.commit()
    await db.refresh(image)

    return ImageStatusResponse.model_validate(image)
```

**Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_admin_images.py -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add app/api/v1/admin.py app/schemas/admin.py
git commit -m "feat: add PATCH /admin/images/{image_id} for direct image moderation"
```

---

## Task 5: Admin Comment Deletion - Tests First

**Files:**
- Modify: `tests/api/v1/test_comments.py` (add tests at end)

**Step 1: Add admin deletion tests**

Add at end of `tests/api/v1/test_comments.py`:

```python
# Add these imports at the top of the file if not present:
# from app.core.security import get_password_hash
# from app.models.permissions import GroupPerms, Groups, Perms, UserGroups


async def create_mod_user(
    db_session: AsyncSession,
    username: str = "moduser",
    email: str = "mod@example.com",
) -> tuple[Users, str]:
    """Create a moderator user and return the user object and password."""
    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def grant_mod_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    from app.models.permissions import GroupPerms, Groups, Perms, UserGroups

    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "comment_mod_group")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="comment_mod_group", desc="Comment mod group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


async def login_as_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.api
class TestAdminCommentDeletion:
    """Tests for admin comment deletion functionality."""

    async def test_mod_can_delete_other_users_comment(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that a user with POST_EDIT permission can delete others' comments."""
        # Create moderator
        mod, mod_password = await create_mod_user(db_session)
        await grant_mod_permission(db_session, mod.user_id, "post_edit")

        # Create image and comment by another user
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        comment = Comments(
            image_id=image.image_id,
            user_id=1,  # Test user from conftest
            post_text="Comment by another user",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        # Login as mod and delete the comment
        token = await login_as_user(client, mod.username, mod_password)

        response = await client.delete(
            f"/api/v1/comments/{comment.post_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True
        assert data["post_text"] == "[deleted]"

    async def test_user_without_permission_cannot_delete_others_comment(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that a user without POST_EDIT cannot delete others' comments."""
        # Create regular user (no permissions)
        regular_user, regular_password = await create_mod_user(
            db_session, username="nomod", email="nomod@example.com"
        )

        # Create image and comment by another user
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        comment = Comments(
            image_id=image.image_id,
            user_id=1,
            post_text="Comment by another user",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        # Login as regular user and try to delete
        token = await login_as_user(client, regular_user.username, regular_password)

        response = await client.delete(
            f"/api/v1/comments/{comment.post_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_owner_can_still_delete_own_comment(
        self, client: AsyncClient, db_session: AsyncSession, sample_image_data: dict
    ):
        """Test that comment owner can still delete their own comment."""
        # Create a user
        owner, owner_password = await create_mod_user(
            db_session, username="commentowner", email="owner@example.com"
        )

        # Create image and comment by owner
        image = Images(**sample_image_data)
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        comment = Comments(
            image_id=image.image_id,
            user_id=owner.user_id,
            post_text="My own comment",
        )
        db_session.add(comment)
        await db_session.commit()
        await db_session.refresh(comment)

        # Login and delete own comment
        token = await login_as_user(client, owner.username, owner_password)

        response = await client.delete(
            f"/api/v1/comments/{comment.post_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["deleted"] is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_comments.py::TestAdminCommentDeletion -v`
Expected: FAIL (permission check not implemented)

**Step 3: Commit test file**

```bash
git add tests/api/v1/test_comments.py
git commit -m "test: add tests for admin comment deletion"
```

---

## Task 6: Implement Admin Comment Deletion

**Files:**
- Modify: `app/api/v1/comments.py:453-503`

**Step 1: Add imports at top of comments.py**

Add these imports if not present:

```python
import redis.asyncio as redis

from app.config import AdminActionType
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models.admin_action import AdminActions
```

**Step 2: Update delete_comment function**

Replace the `delete_comment` function (around line 453):

```python
@router.delete("/{comment_id}", response_model=CommentResponse)
async def delete_comment(
    comment_id: int,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> CommentResponse:
    """
    Delete a comment (soft-delete).

    - Comment owners can delete their own comments
    - Users with POST_EDIT permission can delete any comment

    **Returns:** 200 OK with updated comment (deleted flag set to True)

    **Errors:**
    - 401: Not authenticated
    - 403: User doesn't own the comment and lacks POST_EDIT permission
    - 404: Comment not found
    """
    # Load comment
    result = await db.execute(
        select(Comments).where(Comments.post_id == comment_id)  # type: ignore[arg-type]
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Check authorization: owner or has POST_EDIT permission
    is_owner = comment.user_id == current_user.user_id
    has_mod_permission = await has_permission(
        db, current_user.user_id, Permission.POST_EDIT, redis_client
    )

    if not is_owner and not has_mod_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments",
        )

    # Log admin action if moderator deleting someone else's comment
    if not is_owner and has_mod_permission:
        action = AdminActions(
            user_id=current_user.user_id,
            action_type=AdminActionType.COMMENT_DELETE,
            image_id=comment.image_id,
            details={
                "comment_id": comment_id,
                "original_user_id": comment.user_id,
                "post_text_preview": comment.post_text[:100] if comment.post_text else None,
            },
        )
        db.add(action)

    # Soft delete: Set deleted flag to True
    comment.deleted = True
    comment.post_text = "[deleted]"

    # Detach child comments
    await db.execute(
        update(Comments)
        .where(Comments.parent_comment_id == comment_id)  # type: ignore[arg-type]
        .values(parent_comment_id=None)
    )

    await db.commit()
    await db.refresh(comment)

    return CommentResponse.model_validate(comment)
```

**Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_comments.py::TestAdminCommentDeletion -v`
Expected: All tests PASS

**Step 4: Run all comment tests to ensure no regressions**

Run: `uv run pytest tests/api/v1/test_comments.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add app/api/v1/comments.py
git commit -m "feat: allow POST_EDIT permission holders to delete any comment"
```

---

## Task 7: User Profile Edit Authorization - Tests First

**Files:**
- Modify: `tests/api/v1/test_users.py` (add authorization tests)

**Step 1: Add authorization tests**

Add at end of `tests/api/v1/test_users.py` (or in appropriate test class):

```python
# Add imports at top if not present:
# from app.models.permissions import GroupPerms, Groups, Perms, UserGroups


async def create_test_user_with_password(
    db_session: AsyncSession,
    username: str,
    email: str,
) -> tuple[Users, str]:
    """Create a test user and return user object and password."""
    from app.core.security import get_password_hash

    password = "TestPassword123!"
    user = Users(
        username=username,
        password=get_password_hash(password),
        password_type="bcrypt",
        salt="",
        email=email,
        active=1,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, password


async def grant_user_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    from app.models.permissions import GroupPerms, Groups, Perms, UserGroups

    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(
        select(Groups).where(Groups.title == "user_edit_test_group")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="user_edit_test_group", desc="User edit test group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


async def login_test_user(client: AsyncClient, username: str, password: str) -> str:
    """Login and return the access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.api
class TestUserProfileEditAuthorization:
    """Tests for user profile edit authorization using permission system."""

    async def test_user_can_edit_own_profile(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user can edit their own profile."""
        user, password = await create_test_user_with_password(
            db_session, "selfeditor", "selfeditor@example.com"
        )
        token = await login_test_user(client, user.username, password)

        response = await client.patch(
            f"/api/v1/users/{user.user_id}",
            json={"location": "New York"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["location"] == "New York"

    async def test_user_with_permission_can_edit_others(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user with USER_EDIT_PROFILE permission can edit others."""
        editor, editor_password = await create_test_user_with_password(
            db_session, "profileeditor", "profileeditor@example.com"
        )
        await grant_user_permission(db_session, editor.user_id, "user_edit_profile")

        target, _ = await create_test_user_with_password(
            db_session, "editme", "editme@example.com"
        )

        token = await login_test_user(client, editor.username, editor_password)

        response = await client.patch(
            f"/api/v1/users/{target.user_id}",
            json={"location": "Los Angeles"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["location"] == "Los Angeles"

    async def test_user_without_permission_cannot_edit_others(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that a user without permission cannot edit others."""
        user, password = await create_test_user_with_password(
            db_session, "noeditperm", "noeditperm@example.com"
        )
        target, _ = await create_test_user_with_password(
            db_session, "donteditme", "donteditme@example.com"
        )

        token = await login_test_user(client, user.username, password)

        response = await client.patch(
            f"/api/v1/users/{target.user_id}",
            json={"location": "Chicago"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_users.py::TestUserProfileEditAuthorization -v`
Expected: `test_user_with_permission_can_edit_others` should FAIL (uses admin flag, not permission)

**Step 3: Commit test file**

```bash
git add tests/api/v1/test_users.py
git commit -m "test: add authorization tests for user profile editing"
```

---

## Task 8: Fix User Profile Edit Authorization

**Files:**
- Modify: `app/api/v1/users.py`

**Step 1: Add imports at top of users.py**

Ensure these imports exist:

```python
from app.core.permissions import Permission, has_permission
```

**Step 2: Update update_user_profile function (around line 340)**

Replace the authorization check:

```python
@router.patch("/{user_id}", response_model=UserResponse)
async def update_user_profile(
    user_id: Annotated[int, Path(description="User ID to update")],
    user_data: UserUpdate,
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Update a user's profile.

    - Users can update their own profile
    - Users with USER_EDIT_PROFILE permission can update any user's profile

    All fields are optional. Only provided fields will be updated.
    """
    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    user = await _update_user_profile(user_id, user_data, current_user.user_id, db)
    return UserResponse.model_validate(user)
```

**Step 3: Update upload_user_avatar function (around line 187)**

Replace the authorization check:

```python
@router.post("/{user_id}/avatar", response_model=UserResponse)
async def upload_user_avatar(
    user_id: Annotated[int, Path(description="User ID")],
    avatar: Annotated[UploadFile, File(description="Avatar image file")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Upload avatar for a specified user.

    - Users can upload their own avatar
    - Users with USER_EDIT_PROFILE permission can upload for any user

    Accepts JPG, PNG, or GIF (animated supported). Images are resized to fit
    within 200x200 pixels while preserving aspect ratio. Maximum file size is 1MB.
    """
    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user's avatar",
        )

    return await _upload_avatar(user_id, avatar, db)
```

**Step 4: Update delete_user_avatar function (around line 282)**

Replace the authorization check:

```python
@router.delete("/{user_id}/avatar", response_model=UserResponse)
async def delete_user_avatar(
    user_id: Annotated[int, Path(description="User ID")],
    current_user: Annotated[Users, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> UserResponse:
    """
    Remove avatar for a specified user.

    - Users can delete their own avatar
    - Users with USER_EDIT_PROFILE permission can delete for any user
    """
    # Check permission: user can update themselves, or must have USER_EDIT_PROFILE permission
    is_self = current_user.user_id == user_id
    has_edit_permission = await has_permission(
        db, current_user.user_id, Permission.USER_EDIT_PROFILE, redis_client
    )

    if not is_self and not has_edit_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user's avatar",
        )

    return await _delete_avatar(user_id, db)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_users.py::TestUserProfileEditAuthorization -v`
Expected: All tests PASS

**Step 6: Run all user tests to ensure no regressions**

Run: `uv run pytest tests/api/v1/test_users.py -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
git add app/api/v1/users.py
git commit -m "feat: use USER_EDIT_PROFILE permission instead of admin flag"
```

---

## Task 9: Final Verification

**Step 1: Run full test suite**

Run: `uv run pytest tests/api/v1/ -v`
Expected: All tests PASS

**Step 2: Run linter**

Run: `uv run ruff check app/`
Expected: No errors

**Step 3: Run type checker**

Run: `uv run mypy app/api/v1/admin.py app/api/v1/comments.py app/api/v1/users.py`
Expected: No errors (or only pre-existing ones)

**Step 4: Final commit (if any cleanup needed)**

```bash
git status
# If any remaining changes:
git add -A
git commit -m "chore: cleanup and final fixes"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add AdminActionType constants | `app/config.py` |
| 2 | Add image status schemas | `app/schemas/admin.py` |
| 3 | Write image moderation tests | `tests/api/v1/test_admin_images.py` |
| 4 | Implement image moderation endpoint | `app/api/v1/admin.py` |
| 5 | Write comment deletion tests | `tests/api/v1/test_comments.py` |
| 6 | Implement admin comment deletion | `app/api/v1/comments.py` |
| 7 | Write user authorization tests | `tests/api/v1/test_users.py` |
| 8 | Fix user profile authorization | `app/api/v1/users.py` |
| 9 | Final verification | All |

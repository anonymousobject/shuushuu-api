"""
Tests for image moderation admin endpoints.

These tests cover the PATCH /api/v1/admin/images/{image_id} endpoint.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AdminActionType, ImageStatus
from app.core.security import get_password_hash
from app.models.admin_action import AdminActions
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
        admin=0,
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


@pytest.mark.api
class TestImageStatusChangeAuditLog:
    """Tests for audit logging when changing image status."""

    async def test_status_change_creates_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that changing image status creates IMAGE_STATUS_CHANGE audit entry."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        # Capture IDs before API call to avoid lazy loading issues after expire_all()
        image_id = image.image_id
        admin_user_id = admin.user_id

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image_id}",
            json={"status": ImageStatus.SPOILER},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Expire cached objects to see changes from the API request
        db_session.expire_all()

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.image_id == image_id,
            AdminActions.action_type == AdminActionType.IMAGE_STATUS_CHANGE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin_user_id
        assert action.image_id == image_id
        assert action.details is not None
        assert action.details.get("new_status") == ImageStatus.SPOILER
        assert action.details.get("previous_status") == ImageStatus.ACTIVE

    async def test_repost_audit_includes_replacement_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that marking as repost includes replacement_id in audit details."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")

        original_image = await create_test_image(db_session, admin.user_id)
        repost_image = Images(
            user_id=admin.user_id,
            filename="repost_image",
            ext="jpg",
            md5_hash="auditrepost123456789012345678",
            status=ImageStatus.ACTIVE,
        )
        db_session.add(repost_image)
        await db_session.commit()
        await db_session.refresh(repost_image)

        # Capture IDs before API call to avoid lazy loading issues after expire_all()
        repost_image_id = repost_image.image_id
        original_image_id = original_image.image_id
        admin_user_id = admin.user_id

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{repost_image_id}",
            json={
                "status": ImageStatus.REPOST,
                "replacement_id": original_image_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Expire cached objects to see changes from the API request
        db_session.expire_all()

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.image_id == repost_image_id,
            AdminActions.action_type == AdminActionType.IMAGE_STATUS_CHANGE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin_user_id
        assert action.details is not None
        assert action.details.get("new_status") == ImageStatus.REPOST
        assert action.details.get("replacement_id") == original_image_id


@pytest.mark.api
class TestImageLocked:
    """Tests for locking/unlocking image comments."""

    async def test_lock_image_comments(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test locking comments on an image."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        assert image.locked == 0

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"locked": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["locked"] == 1

        # Verify in database
        db_session.expire_all()
        await db_session.refresh(image)
        assert image.locked == 1

    async def test_unlock_image_comments(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test unlocking comments on an image."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        # Start with locked image
        image.locked = 1
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"locked": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["locked"] == 0

        # Verify in database
        db_session.expire_all()
        await db_session.refresh(image)
        assert image.locked == 0

    async def test_change_status_and_locked_together(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test changing both status and locked in one request."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"status": ImageStatus.SPOILER, "locked": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == ImageStatus.SPOILER
        assert data["locked"] == 1

    async def test_locked_only_without_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that locked can be changed without providing status."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        original_status = image.status

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={"locked": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["locked"] == 1
        assert data["status"] == original_status  # Status unchanged

    async def test_locked_audit_log(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that locking an image creates an audit log entry."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        image_id = image.image_id
        admin_user_id = admin.user_id

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image_id}",
            json={"locked": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        db_session.expire_all()

        # Verify audit log entry
        stmt = select(AdminActions).where(
            AdminActions.image_id == image_id,
            AdminActions.action_type == AdminActionType.IMAGE_STATUS_CHANGE,
        )
        result = await db_session.execute(stmt)
        action = result.scalar_one_or_none()

        assert action is not None
        assert action.user_id == admin_user_id
        assert action.details is not None
        assert action.details.get("previous_locked") == 0
        assert action.details.get("new_locked") == 1
        # Verify status fields are recorded even when only locked changes
        assert action.details.get("previous_status") == ImageStatus.ACTIVE
        assert action.details.get("new_status") == ImageStatus.ACTIVE
        assert action.details.get("replacement_id") is None

    async def test_must_provide_status_or_locked(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that at least one of status or locked must be provided."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "image_edit")
        image = await create_test_image(db_session, admin.user_id)

        token = await login_user(client, admin.username, admin_password)

        response = await client.patch(
            f"/api/v1/admin/images/{image.image_id}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422  # Validation error

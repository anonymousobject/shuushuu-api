"""
Tests for user suspension admin endpoints.

These tests cover the /api/v1/admin/users/{user_id}/* endpoints including:
- Suspending users (temporary and permanent)
- Reactivating users
- Viewing suspension history
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.refresh_token import RefreshTokens
from app.models.user import Users
from app.models.user_suspension import UserSuspensions


async def create_admin_user(
    db_session: AsyncSession,
    username: str = "suspensionadmin",
    email: str = "suspensionadmin@example.com",
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
    active: int = 1,
) -> Users:
    """Create a regular user."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=email,
        active=active,
        admin=0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    # Get or create the permission
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    # Get or create a test group
    result = await db_session.execute(
        select(Groups).where(Groups.title == "suspension_test_admin")
    )
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="suspension_test_admin", desc="Suspension test admin group")
        db_session.add(group)
        await db_session.flush()

    # Grant permission to group if not already granted
    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    # Add user to group if not already in it
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


@pytest.mark.api
class TestSuspendUser:
    """Tests for POST /api/v1/admin/users/{user_id}/suspend endpoint."""

    async def test_suspend_user_temporary(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test suspending a user with expiration date."""
        # Create admin and regular users
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="suspendme")

        # Login as admin
        token = await login_user(client, admin.username, admin_password)

        # Suspend the user
        suspend_until = datetime.now(UTC) + timedelta(days=7)
        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "suspended_until": suspend_until.isoformat(),
                "reason": "Violated community guidelines",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "suspended" in response.json()["message"].lower()

        # Verify user is inactive
        await db_session.refresh(target_user)
        assert target_user.active == 0

        # Verify suspension record created
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == target_user.user_id)
            .where(UserSuspensions.action == "suspended")
        )
        suspension = result.scalar_one()
        assert suspension.actioned_by == admin.user_id
        assert suspension.reason == "Violated community guidelines"
        assert suspension.suspended_until is not None

        # Verify all refresh tokens were revoked
        result = await db_session.execute(
            select(RefreshTokens).where(RefreshTokens.user_id == target_user.user_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_suspend_user_permanent(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test permanent suspension (no expiration)."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="banme")

        token = await login_user(client, admin.username, admin_password)

        # Suspend without expiration (permanent)
        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "suspended_until": None,
                "reason": "Severe violation - permanent ban",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify suspension record has no expiration
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == target_user.user_id)
            .where(UserSuspensions.action == "suspended")
        )
        suspension = result.scalar_one()
        assert suspension.suspended_until is None
        assert "permanent" in suspension.reason.lower()

    async def test_suspend_already_suspended_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test suspending a user who is already suspended."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="alreadysuspended")

        # Manually suspend the user first
        target_user.active = 0
        # Use naive datetime to match database storage
        suspend_until = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=target_user.user_id,
            action="suspended",
            actioned_by=admin.user_id,
            reason="First suspension",
            suspended_until=suspend_until,
        )
        db_session.add(suspension)
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        # Try to suspend again
        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "suspended_until": None,
                "reason": "Trying to suspend again",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "already suspended" in response.json()["detail"].lower()

    async def test_suspend_nonexistent_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test suspending a user that doesn't exist."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            "/api/v1/admin/users/99999/suspend",
            json={
                "suspended_until": None,
                "reason": "This user doesn't exist",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_suspend_self_prevention(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that admins cannot suspend themselves."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{admin.user_id}/suspend",
            json={
                "suspended_until": None,
                "reason": "Trying to suspend myself",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "cannot suspend yourself" in response.json()["detail"].lower()

    async def test_suspend_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test suspending without USER_BAN permission."""
        admin, admin_password = await create_admin_user(db_session, username="nopermadmin")
        # Don't grant USER_BAN permission
        target_user = await create_regular_user(db_session, username="targetuser")

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "suspended_until": None,
                "reason": "No permission to do this",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_suspend_reason_too_short(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that suspension reason must be at least 3 characters."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="shortreason")

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "suspended_until": None,
                "reason": "AB",  # Only 2 characters
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422


@pytest.mark.api
class TestReactivateUser:
    """Tests for POST /api/v1/admin/users/{user_id}/reactivate endpoint."""

    async def test_reactivate_suspended_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reactivating a suspended user."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(
            db_session, username="reactivateme", active=0
        )

        # Create suspension record (use naive datetime for DB storage)
        suspend_until = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=target_user.user_id,
            action="suspended",
            actioned_by=admin.user_id,
            reason="Was suspended",
            suspended_until=suspend_until,
        )
        db_session.add(suspension)
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        # Reactivate the user
        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/reactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "reactivated" in response.json()["message"].lower()

        # Verify user is active
        await db_session.refresh(target_user)
        assert target_user.active == 1

        # Verify reactivation record created
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == target_user.user_id)
            .where(UserSuspensions.action == "reactivated")
        )
        reactivation = result.scalar_one()
        assert reactivation.actioned_by == admin.user_id

    async def test_reactivate_already_active_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reactivating a user who is already active without suspension record."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(
            db_session, username="alreadyactive", active=1
        )
        # No suspension record - user was never suspended

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/reactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should fail because there's no suspension record
        assert response.status_code == 400
        assert "no suspension record" in response.json()["detail"].lower()

    async def test_reactivate_user_no_suspension_record(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reactivating an inactive user with no suspension record."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(
            db_session, username="nosuspension", active=0
        )
        # No suspension record created

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/reactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "no suspension record" in response.json()["detail"].lower()

    async def test_reactivate_already_reactivated_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reactivating a user who was already reactivated."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(
            db_session, username="wasreactivated", active=0
        )

        # Create suspension and reactivation records (use naive datetimes)
        now = datetime.now(UTC).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=target_user.user_id,
            action="suspended",
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=2),
            reason="Was suspended",
        )
        db_session.add(suspension)

        reactivation = UserSuspensions(
            user_id=target_user.user_id,
            action="reactivated",
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=1),
        )
        db_session.add(reactivation)
        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/reactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400
        assert "not currently suspended" in response.json()["detail"].lower()

    async def test_reactivate_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test reactivating without USER_BAN permission."""
        admin, admin_password = await create_admin_user(
            db_session, username="nopermreactivate"
        )
        target_user = await create_regular_user(db_session, username="target", active=0)

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/reactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403


@pytest.mark.api
class TestUserSuspensionHistory:
    """Tests for GET /api/v1/admin/users/{user_id}/suspensions endpoint."""

    async def test_get_suspension_history(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test retrieving user suspension history."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="historyuser")

        # Create multiple suspension records (use naive datetimes)
        now = datetime.now(UTC).replace(tzinfo=None)
        suspension1 = UserSuspensions(
            user_id=target_user.user_id,
            action="suspended",
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=10),
            reason="First suspension",
            suspended_until=now - timedelta(days=5),
        )
        db_session.add(suspension1)

        reactivation1 = UserSuspensions(
            user_id=target_user.user_id,
            action="reactivated",
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=5),
        )
        db_session.add(reactivation1)

        suspension2 = UserSuspensions(
            user_id=target_user.user_id,
            action="suspended",
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=2),
            reason="Second suspension",
            suspended_until=now + timedelta(days=7),
        )
        db_session.add(suspension2)

        await db_session.commit()

        token = await login_user(client, admin.username, admin_password)

        # Get suspension history
        response = await client.get(
            f"/api/v1/admin/users/{target_user.user_id}/suspensions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == target_user.user_id
        assert data["username"] == target_user.username
        assert data["total"] == 3
        assert len(data["suspensions"]) == 3

        # Verify order (most recent first)
        assert data["suspensions"][0]["action"] == "suspended"
        assert data["suspensions"][0]["reason"] == "Second suspension"
        assert data["suspensions"][1]["action"] == "reactivated"
        assert data["suspensions"][2]["action"] == "suspended"
        assert data["suspensions"][2]["reason"] == "First suspension"

    async def test_get_suspension_history_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting history for user with no suspensions."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="cleanuser")

        token = await login_user(client, admin.username, admin_password)

        response = await client.get(
            f"/api/v1/admin/users/{target_user.user_id}/suspensions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["suspensions"]) == 0

    async def test_get_suspension_history_nonexistent_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting history for non-existent user."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")

        token = await login_user(client, admin.username, admin_password)

        response = await client.get(
            "/api/v1/admin/users/99999/suspensions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    async def test_get_suspension_history_without_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting history without USER_BAN permission."""
        admin, admin_password = await create_admin_user(
            db_session, username="nopermhistory"
        )
        target_user = await create_regular_user(db_session, username="someuser")

        token = await login_user(client, admin.username, admin_password)

        response = await client.get(
            f"/api/v1/admin/users/{target_user.user_id}/suspensions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

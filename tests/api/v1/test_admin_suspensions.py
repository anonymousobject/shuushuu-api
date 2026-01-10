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

from app.config import SuspensionAction
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
            .where(UserSuspensions.action == SuspensionAction.SUSPENDED)
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
            .where(UserSuspensions.action == SuspensionAction.SUSPENDED)
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
            action=SuspensionAction.SUSPENDED,
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
        assert "yourself" in response.json()["detail"].lower()

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

    async def test_warn_user(self, client: AsyncClient, db_session: AsyncSession):
        """Test issuing a warning to a user (no suspension)."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="warnme")

        token = await login_user(client, admin.username, admin_password)

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "action": "warning",
                "reason": "This is a formal warning",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "warning" in response.json()["message"].lower()

        # Verify user is still active
        await db_session.refresh(target_user)
        assert target_user.active == 1

        # Verify warning record was created
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == target_user.user_id)
            .where(UserSuspensions.action == SuspensionAction.WARNING)
        )
        warning = result.scalar_one()
        assert warning.actioned_by == admin.user_id
        assert warning.reason == "This is a formal warning"
        assert warning.suspended_until is None

    async def test_warn_user_suspended_until_ignored(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that suspended_until is ignored for warnings."""
        admin, admin_password = await create_admin_user(db_session)
        await grant_permission(db_session, admin.user_id, "user_ban")
        target_user = await create_regular_user(db_session, username="warnignore")

        token = await login_user(client, admin.username, admin_password)

        future_date = (datetime.now(UTC) + timedelta(days=7)).isoformat()

        response = await client.post(
            f"/api/v1/admin/users/{target_user.user_id}/suspend",
            json={
                "action": "warning",
                "suspended_until": future_date,  # Should be ignored
                "reason": "Warning with ignored expiry",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

        # Verify suspended_until is None despite being provided
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == target_user.user_id)
            .where(UserSuspensions.action == SuspensionAction.WARNING)
        )
        warning = result.scalar_one()
        assert warning.suspended_until is None


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
            action=SuspensionAction.SUSPENDED,
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
            .where(UserSuspensions.action == SuspensionAction.REACTIVATED)
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
            action=SuspensionAction.SUSPENDED,
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=2),
            reason="Was suspended",
        )
        db_session.add(suspension)

        reactivation = UserSuspensions(
            user_id=target_user.user_id,
            action=SuspensionAction.REACTIVATED,
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
            action=SuspensionAction.SUSPENDED,
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=10),
            reason="First suspension",
            suspended_until=now - timedelta(days=5),
        )
        db_session.add(suspension1)

        reactivation1 = UserSuspensions(
            user_id=target_user.user_id,
            action=SuspensionAction.REACTIVATED,
            actioned_by=admin.user_id,
            actioned_at=now - timedelta(days=5),
        )
        db_session.add(reactivation1)

        suspension2 = UserSuspensions(
            user_id=target_user.user_id,
            action=SuspensionAction.SUSPENDED,
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


@pytest.mark.api
class TestUserWarningAcknowledgement:
    """Tests for GET/POST /api/v1/users/me/warnings endpoints."""

    async def test_get_unacknowledged_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting unacknowledged warnings for current user."""
        user = await create_regular_user(db_session, username="warneduser")
        admin, _ = await create_admin_user(db_session)

        # Create an unacknowledged warning
        warning = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.WARNING,
            actioned_by=admin.user_id,
            reason="This is a warning",
        )
        db_session.add(warning)
        await db_session.commit()

        token = await login_user(client, user.username, "TestPassword123!")

        response = await client.get(
            "/api/v1/users/me/warnings",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["action"] == "warning"
        assert data["items"][0]["reason"] == "This is a warning"

    async def test_get_warnings_excludes_acknowledged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that acknowledged warnings are not returned."""
        user = await create_regular_user(db_session, username="ackwarnuser")
        admin, _ = await create_admin_user(db_session)

        now = datetime.now(UTC).replace(tzinfo=None)

        # Create an acknowledged warning
        acknowledged_warning = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.WARNING,
            actioned_by=admin.user_id,
            reason="Already acknowledged",
            acknowledged_at=now,
        )
        db_session.add(acknowledged_warning)

        # Create an unacknowledged warning
        unacknowledged_warning = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.WARNING,
            actioned_by=admin.user_id,
            reason="Not yet acknowledged",
        )
        db_session.add(unacknowledged_warning)
        await db_session.commit()

        token = await login_user(client, user.username, "TestPassword123!")

        response = await client.get(
            "/api/v1/users/me/warnings",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["items"][0]["reason"] == "Not yet acknowledged"

    async def test_get_warnings_excludes_reactivated(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that reactivation records are not returned as warnings."""
        user = await create_regular_user(db_session, username="reactivateduser")
        admin, _ = await create_admin_user(db_session)

        # Create a reactivation record (should not appear as warning)
        reactivation = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.REACTIVATED,
            actioned_by=admin.user_id,
        )
        db_session.add(reactivation)
        await db_session.commit()

        token = await login_user(client, user.username, "TestPassword123!")

        response = await client.get(
            "/api/v1/users/me/warnings",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    async def test_acknowledge_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test acknowledging all warnings."""
        user = await create_regular_user(db_session, username="ackuser")
        admin, _ = await create_admin_user(db_session)

        # Create multiple unacknowledged warnings
        warning1 = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.WARNING,
            actioned_by=admin.user_id,
            reason="Warning 1",
        )
        warning2 = UserSuspensions(
            user_id=user.user_id,
            action=SuspensionAction.WARNING,
            actioned_by=admin.user_id,
            reason="Warning 2",
        )
        db_session.add(warning1)
        db_session.add(warning2)
        await db_session.commit()

        token = await login_user(client, user.username, "TestPassword123!")

        # Acknowledge warnings
        response = await client.post(
            "/api/v1/users/me/warnings/acknowledge",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["acknowledged_count"] == 2

        # Verify warnings are now acknowledged
        await db_session.refresh(warning1)
        await db_session.refresh(warning2)
        assert warning1.acknowledged_at is not None
        assert warning2.acknowledged_at is not None

        # Get warnings again - should be empty
        response = await client.get(
            "/api/v1/users/me/warnings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.json()["count"] == 0

    async def test_acknowledge_no_warnings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test acknowledging when there are no warnings."""
        user = await create_regular_user(db_session, username="nowarningsuser")

        token = await login_user(client, user.username, "TestPassword123!")

        response = await client.post(
            "/api/v1/users/me/warnings/acknowledge",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["acknowledged_count"] == 0
        assert "no warnings" in data["message"].lower()

    async def test_warnings_requires_authentication(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that warnings endpoints require authentication."""
        response = await client.get("/api/v1/users/me/warnings")
        assert response.status_code == 401

        response = await client.post("/api/v1/users/me/warnings/acknowledge")
        assert response.status_code == 401

"""
Tests for authentication API endpoints.

These tests cover the /api/v1/auth endpoints including:
- User login
- Token refresh
- Logout
- Change password
- Suspension checks during login and refresh
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.refresh_token import RefreshTokens
from app.models.user import Users
from app.models.user_suspension import UserSuspensions


@pytest.mark.api
class TestLogin:
    """Tests for POST /api/v1/auth/login endpoint."""

    async def test_login_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful user login."""
        # Create user with known password
        user = Users(
            username="loginuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="login@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "loginuser", "password": "TestPassword123!"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

        # Check that refresh token cookie is set
        assert "refresh_token" in response.cookies

    async def test_login_wrong_password(self, client: AsyncClient, db_session: AsyncSession):
        """Test login with incorrect password."""
        user = Users(
            username="loginuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="login@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "loginuser", "password": "WrongPassword"},
        )

        assert response.status_code == 401
        assert "incorrect username or password" in response.json()["detail"].lower()

    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with non-existent username."""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "SomePassword123!"},
        )

        assert response.status_code == 401
        assert "incorrect username or password" in response.json()["detail"].lower()

    async def test_login_inactive_user(self, client: AsyncClient, db_session: AsyncSession):
        """Test login with inactive user account."""
        user = Users(
            username="inactiveuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="inactive@example.com",
            active=0,  # Inactive
        )
        db_session.add(user)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "inactiveuser", "password": "TestPassword123!"},
        )

        assert response.status_code == 401
        assert "inactive" in response.json()["detail"].lower()

    async def test_login_creates_refresh_token(self, client: AsyncClient, db_session: AsyncSession):
        """Test that login creates a refresh token in the database."""
        user = Users(
            username="tokenuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="token@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # No tokens before login
        result = await db_session.execute(
            select(RefreshTokens).where(RefreshTokens.user_id == user.user_id)
        )
        assert result.scalar_one_or_none() is None

        # Login
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "tokenuser", "password": "TestPassword123!"},
        )
        assert response.status_code == 200

        # Token should exist after login
        result = await db_session.execute(
            select(RefreshTokens).where(RefreshTokens.user_id == user.user_id)
        )
        token = result.scalar_one_or_none()
        assert token is not None
        assert token.revoked is False


@pytest.mark.api
class TestRefresh:
    """Tests for POST /api/v1/auth/refresh endpoint."""

    async def test_refresh_token_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful token refresh."""
        # Create user and login
        user = Users(
            username="refreshuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="refresh@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get tokens
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "refreshuser", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200

        # Refresh token
        refresh_response = await client.post("/api/v1/auth/refresh")
        assert refresh_response.status_code == 200
        data = refresh_response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_refresh_without_token(self, client: AsyncClient):
        """Test refresh without a refresh token cookie."""
        response = await client.post("/api/v1/auth/refresh")
        assert response.status_code == 401


@pytest.mark.api
class TestLogout:
    """Tests for POST /api/v1/auth/logout endpoint."""

    async def test_logout_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful logout."""
        # Create user and login
        user = Users(
            username="logoutuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="logout@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "logoutuser", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200

        # Logout
        logout_response = await client.post("/api/v1/auth/logout")
        assert logout_response.status_code == 200
        assert logout_response.json()["message"] == "Successfully logged out"

    async def test_logout_without_token(self, client: AsyncClient):
        """Test logout without being logged in."""
        response = await client.post("/api/v1/auth/logout")
        assert response.status_code == 401


@pytest.mark.api
class TestGetCurrentUser:
    """Tests for GET /api/v1/auth/me endpoint."""

    async def test_get_current_user_authenticated(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test getting current user info when authenticated."""
        # Create user and login
        user = Users(
            username="meuser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="me@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "meuser", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Get current user
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "meuser"
        assert data["email"] == "me@example.com"

    async def test_get_current_user_unauthenticated(self, client: AsyncClient):
        """Test getting current user without authentication."""
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401


@pytest.mark.api
class TestChangePassword:
    """Tests for POST /api/v1/auth/change-password endpoint."""

    async def test_change_password_success(self, client: AsyncClient, db_session: AsyncSession):
        """Test successful password change."""
        # Create user
        user = Users(
            username="changeuser",
            password=get_password_hash("OldPassword123!"),
            password_type="bcrypt",
            salt="",
            email="change@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "changeuser", "password": "OldPassword123!"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Change password
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "OldPassword123!",
                "new_password": "NewPassword456!",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert "password changed successfully" in response.json()["message"].lower()

        # Verify can login with new password
        new_login = await client.post(
            "/api/v1/auth/login",
            json={"username": "changeuser", "password": "NewPassword456!"},
        )
        assert new_login.status_code == 200

    async def test_change_password_wrong_current(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test password change with incorrect current password."""
        # Create user
        user = Users(
            username="changeuser2",
            password=get_password_hash("CurrentPassword123!"),
            password_type="bcrypt",
            salt="",
            email="change2@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "changeuser2", "password": "CurrentPassword123!"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Try to change password with wrong current password
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "WrongPassword!",
                "new_password": "NewPassword456!",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 400
        assert "incorrect" in response.json()["detail"].lower()

    async def test_change_password_unauthenticated(self, client: AsyncClient):
        """Test password change without authentication."""
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "OldPassword123!",
                "new_password": "NewPassword456!",
            },
        )
        assert response.status_code == 401


@pytest.mark.api
class TestLoginSuspensionCheck:
    """Tests for suspension checking during login."""

    async def test_login_blocked_by_active_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that login is blocked when user has an active suspension."""
        # Create suspended user
        user = Users(
            username="suspendedlogin",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="suspended@example.com",
            active=0,  # Suspended
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create active suspension record (use naive datetime for DB storage)
        suspend_until = (datetime.now(UTC) + timedelta(days=7)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=user.user_id,
            action="suspended",
            actioned_by=1,
            reason="You violated our terms of service",
            suspended_until=suspend_until,
        )
        db_session.add(suspension)
        await db_session.commit()

        # Attempt login
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "suspendedlogin", "password": "TestPassword123!"},
        )

        assert response.status_code == 403
        assert "violated our terms" in response.json()["detail"].lower()

    async def test_login_blocked_by_permanent_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that login is blocked when user has a permanent suspension."""
        user = Users(
            username="permaban",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="permaban@example.com",
            active=0,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Permanent suspension (no expiration)
        suspension = UserSuspensions(
            user_id=user.user_id,
            action="suspended",
            actioned_by=1,
            reason="Permanent ban for severe violations",
            suspended_until=None,
        )
        db_session.add(suspension)
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "permaban", "password": "TestPassword123!"},
        )

        assert response.status_code == 403
        assert "permanent" in response.json()["detail"].lower()

    async def test_login_auto_reactivates_expired_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that login auto-reactivates user when suspension has expired."""
        user = Users(
            username="expiredban",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="expired@example.com",
            active=0,  # Still inactive from old suspension
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create expired suspension record (use naive datetime)
        suspend_until = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=user.user_id,
            action="suspended",
            actioned_by=1,
            reason="Old suspension",
            suspended_until=suspend_until,  # Expired yesterday
        )
        db_session.add(suspension)
        await db_session.commit()

        # Login should succeed and auto-reactivate
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "expiredban", "password": "TestPassword123!"},
        )

        assert response.status_code == 200
        assert "access_token" in response.json()

        # Verify user was reactivated
        await db_session.refresh(user)
        assert user.active == 1

        # Verify reactivation record was created
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user.user_id)
            .where(UserSuspensions.action == "reactivated")
        )
        reactivation = result.scalar_one()
        assert reactivation.actioned_by is None  # Auto-reactivated

    async def test_login_inactive_user_without_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that inactive users without suspension records get appropriate error."""
        user = Users(
            username="inactivenosuspension",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="inactivenosuspension@example.com",
            active=0,
        )
        db_session.add(user)
        await db_session.commit()
        # No suspension record created

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "inactivenosuspension", "password": "TestPassword123!"},
        )

        assert response.status_code == 401
        assert "inactive" in response.json()["detail"].lower()


@pytest.mark.api
class TestRefreshSuspensionCheck:
    """Tests for suspension checking during token refresh."""

    async def test_refresh_blocked_by_active_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that token refresh is blocked when user has an active suspension."""
        # Create user and login first
        user = Users(
            username="refreshsuspended",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="refreshsuspended@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get tokens
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "refreshsuspended", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200

        # Now suspend the user (use naive datetime)
        user.active = 0
        suspend_until = (datetime.now(UTC) + timedelta(days=7)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=user.user_id,
            action="suspended",
            actioned_by=1,
            reason="Suspended after login",
            suspended_until=suspend_until,
        )
        db_session.add(suspension)
        await db_session.commit()

        # Try to refresh token (should be blocked)
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == 403
        assert "suspended" in response.json()["detail"].lower()

    async def test_refresh_auto_reactivates_expired_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that token refresh auto-reactivates user when suspension has expired."""
        # Create user and login
        user = Users(
            username="refreshexpired",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="refreshexpired@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get tokens
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "refreshexpired", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200

        # Suspend user with expired suspension (use naive datetime)
        user.active = 0
        suspend_until = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
        suspension = UserSuspensions(
            user_id=user.user_id,
            action="suspended",
            actioned_by=1,
            reason="Old suspension",
            suspended_until=suspend_until,  # Expired
        )
        db_session.add(suspension)
        await db_session.commit()

        # Refresh should succeed and auto-reactivate
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == 200
        assert "access_token" in response.json()

        # Verify user was reactivated
        await db_session.refresh(user)
        assert user.active == 1

        # Verify reactivation record was created
        result = await db_session.execute(
            select(UserSuspensions)
            .where(UserSuspensions.user_id == user.user_id)
            .where(UserSuspensions.action == "reactivated")
        )
        reactivation = result.scalar_one()
        assert reactivation.actioned_by is None  # Auto-reactivated

    async def test_refresh_inactive_user_without_suspension(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that inactive users without suspension records get appropriate error on refresh."""
        # Create user and login
        user = Users(
            username="refreshinactive",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="refreshinactive@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get tokens
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "refreshinactive", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200

        # Deactivate user without creating suspension record
        user.active = 0
        await db_session.commit()

        # Try to refresh (should fail with inactive error)
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        assert "inactive" in response.json()["detail"].lower()

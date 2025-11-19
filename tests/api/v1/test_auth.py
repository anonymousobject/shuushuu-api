"""
Tests for authentication API endpoints.

These tests cover the /api/v1/auth endpoints including:
- User login
- Token refresh
- Logout
- Change password
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.refresh_token import RefreshTokens
from app.models.user import Users


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

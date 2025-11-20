"""
Tests for private messages API endpoints.

These tests cover the /api/v1/privmsgs endpoints including:
- Get received private messages
- Get sent private messages
- Admin filtering by user_id
- Permission checks (users can only see their own messages)
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.privmsg import Privmsgs
from app.models.user import Users


@pytest.mark.api
class TestGetReceivedPrivmsgs:
    """Tests for GET /api/v1/privmsgs/received endpoint."""

    async def test_get_own_received_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user getting their own received messages."""
        # Create user
        user = Users(
            username="msguser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="msguser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create messages sent to user (received)
        for i in range(3):
            msg = Privmsgs(
                from_user_id=2,
                to_user_id=user.user_id,
                text=f"Message {i} to user",
                date=datetime.now(UTC),
            )
            db_session.add(msg)

        # Create messages sent by user (should NOT appear in received)
        for i in range(2):
            msg = Privmsgs(
                from_user_id=user.user_id,
                to_user_id=3,
                text=f"Message {i} from user",
                date=datetime.now(UTC),
            )
            db_session.add(msg)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "msguser", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Get received messages
        response = await client.get(
            "/api/v1/privmsgs/received",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # Should only see received messages (3)
        assert data["total"] == 3
        for msg in data["messages"]:
            assert msg["to_user_id"] == user.user_id

    async def test_get_received_messages_unauthenticated(self, client: AsyncClient):
        """Test getting received messages without authentication."""
        response = await client.get("/api/v1/privmsgs/received")
        assert response.status_code == 401

    async def test_admin_get_other_user_received_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin viewing another user's received messages."""
        # Create admin user
        admin = Users(
            username="adminuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()

        # Create message to user 2
        msg = Privmsgs(
            from_user_id=1,
            to_user_id=2,
            text="Message to user 2",
            date=datetime.now(UTC),
        )
        db_session.add(msg)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Admin views user 2's received messages
        response = await client.get(
            "/api/v1/privmsgs/received?user_id=2",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        for msg in data["messages"]:
            assert msg["to_user_id"] == 2

    async def test_non_admin_cannot_view_other_user_received_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot view other user's received messages."""
        # Create regular user
        user = Users(
            username="regularuser",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regular@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularuser", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to view user 2's received messages
        response = await client.get(
            "/api/v1/privmsgs/received?user_id=2",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403


@pytest.mark.api
class TestGetSentPrivmsgs:
    """Tests for GET /api/v1/privmsgs/sent endpoint."""

    async def test_get_own_sent_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user getting their own sent messages."""
        # Create user
        user = Users(
            username="senduser",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="senduser@example.com",
            active=1,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create messages sent by user
        for i in range(3):
            msg = Privmsgs(
                from_user_id=user.user_id,
                to_user_id=2,
                text=f"Message {i} from user",
                date=datetime.now(UTC),
            )
            db_session.add(msg)

        # Create messages received by user (should NOT appear in sent)
        for i in range(2):
            msg = Privmsgs(
                from_user_id=3,
                to_user_id=user.user_id,
                text=f"Message {i} to user",
                date=datetime.now(UTC),
            )
            db_session.add(msg)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "senduser", "password": "TestPassword123!"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Get sent messages
        response = await client.get(
            "/api/v1/privmsgs/sent",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # Should only see sent messages (3)
        assert data["total"] == 3
        for msg in data["messages"]:
            assert msg["from_user_id"] == user.user_id

    async def test_get_sent_messages_unauthenticated(self, client: AsyncClient):
        """Test getting sent messages without authentication."""
        response = await client.get("/api/v1/privmsgs/sent")
        assert response.status_code == 401

    async def test_admin_get_other_user_sent_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test admin viewing another user's sent messages."""
        # Create admin user
        admin = Users(
            username="adminuser2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin2@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()

        # Create message from user 1
        msg = Privmsgs(
            from_user_id=1,
            to_user_id=2,
            text="Message from user 1",
            date=datetime.now(UTC),
        )
        db_session.add(msg)
        await db_session.commit()

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "adminuser2", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Admin views user 1's sent messages
        response = await client.get(
            "/api/v1/privmsgs/sent?user_id=1",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        for msg in data["messages"]:
            assert msg["from_user_id"] == 1

    async def test_non_admin_cannot_view_other_user_sent_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test non-admin cannot view other user's sent messages."""
        # Create regular user
        user = Users(
            username="regularuser2",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="regular2@example.com",
            active=1,
            admin=0,
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "regularuser2", "password": "Password123!"},
        )
        access_token = login_response.json()["access_token"]

        # Try to view user 2's sent messages
        response = await client.get(
            "/api/v1/privmsgs/sent?user_id=2",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 403

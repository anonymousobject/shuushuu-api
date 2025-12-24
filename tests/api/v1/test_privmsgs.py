"""
Tests for private messages API endpoints.

These tests cover the /api/v1/privmsgs endpoints including:
- Get received private messages
- Get sent private messages
- Permission-based filtering by user_id
- Permission checks (users can only see their own messages)
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.security import get_password_hash
from app.models.permissions import Perms, UserPerms
from app.models.privmsg import Privmsgs
from app.models.user import Users
from app.config import settings


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

        # Create a sender user with an avatar and messages sent to user (received)
        sender = Users(
            username="sendr",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="sender@example.com",
            active=1,
            avatar="sender.png",
        )
        db_session.add(sender)
        await db_session.commit()
        await db_session.refresh(sender)

        for i in range(3):
            msg = Privmsgs(
                from_user_id=sender.user_id,
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
            # Avatar URL should be present and correctly generated
            assert msg.get("from_avatar_url") == f"{settings.IMAGE_BASE_URL}/images/avatars/sender.png"

    async def test_get_received_messages_unauthenticated(self, client: AsyncClient):
        """Test getting received messages without authentication."""
        response = await client.get("/api/v1/privmsgs/received")
        assert response.status_code == 401

    async def test_admin_get_other_user_received_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with PRIVMSG_VIEW permission viewing another user's received messages."""
        # Create PRIVMSG_VIEW permission if it doesn't exist
        perm = Perms(title="privmsg_view", desc="View private messages")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create user with PRIVMSG_VIEW permission
        user_with_perm = Users(
            username="adminuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin@example.com",
            active=1,
        )
        db_session.add(user_with_perm)
        await db_session.commit()
        await db_session.refresh(user_with_perm)

        # Grant PRIVMSG_VIEW permission
        user_perm = UserPerms(
            user_id=user_with_perm.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
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

        # Login as user with permission
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

    async def test_delete_privmsg_marks_to_del_for_recipient(self, client: AsyncClient, db_session: AsyncSession):
        """Test recipient deleting a message marks to_del and preserves row if sender hasn't deleted."""
        # Create recipient
        recipient = Users(
            username="delrecipient",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delrecipient@example.com",
            active=1,
        )
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(recipient)

        # Create message to recipient
        msg = Privmsgs(from_user_id=2, to_user_id=recipient.user_id, text="Please delete me")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        # Login as recipient
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delrecipient", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Delete message
        response = await client.delete(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 204

        # Refresh from DB
        await db_session.refresh(msg)
        assert msg.to_del == 1

    async def test_delete_privmsg_deleted_when_both_deleted(self, client: AsyncClient, db_session: AsyncSession):
        """Test that message row is removed when both parties have deleted it."""
        # Create sender and recipient
        sender = Users(
            username="delsender",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delsender@example.com",
            active=1,
        )
        recipient = Users(
            username="delrecipient2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="delrecipient2@example.com",
            active=1,
        )
        db_session.add(sender)
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(sender)
        await db_session.refresh(recipient)

        # Create message
        msg = Privmsgs(from_user_id=sender.user_id, to_user_id=recipient.user_id, text="Temp message")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        # Sender deletes
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delsender", "password": "TestPassword123!"},
        )
        access_token_sender = login_response.json()["access_token"]
        response = await client.delete(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token_sender}"})
        assert response.status_code == 204

        # Recipient deletes, should remove row
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "delrecipient2", "password": "TestPassword123!"},
        )
        access_token_recipient = login_response.json()["access_token"]
        response = await client.delete(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token_recipient}"})
        assert response.status_code == 204

        # Check that message no longer exists
        result = await db_session.execute(select(Privmsgs).where(Privmsgs.privmsg_id == msg.privmsg_id))
        assert result.scalar_one_or_none() is None


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
        # Create a recipient with an avatar and messages sent by user
        recipient = Users(
            username="recv",
            password=get_password_hash("Password123!"),
            password_type="bcrypt",
            salt="",
            email="recv@example.com",
            active=1,
            avatar="recv.png",
        )
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(recipient)

        for i in range(3):
            msg = Privmsgs(
                from_user_id=user.user_id,
                to_user_id=recipient.user_id,
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
            # Avatar URL for recipient should be present
            assert msg.get("to_avatar_url") == f"{settings.IMAGE_BASE_URL}/images/avatars/recv.png"

    async def test_get_sent_messages_unauthenticated(self, client: AsyncClient):
        """Test getting sent messages without authentication."""
        response = await client.get("/api/v1/privmsgs/sent")
        assert response.status_code == 401

    async def test_admin_get_other_user_sent_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test user with PRIVMSG_VIEW permission viewing another user's sent messages."""
        # Create PRIVMSG_VIEW permission if it doesn't exist
        perm = Perms(title="privmsg_view", desc="View private messages")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create user with PRIVMSG_VIEW permission
        user_with_perm = Users(
            username="adminuser2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="admin2@example.com",
            active=1,
        )
        db_session.add(user_with_perm)
        await db_session.commit()
        await db_session.refresh(user_with_perm)

        # Grant PRIVMSG_VIEW permission
        user_perm = UserPerms(
            user_id=user_with_perm.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
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

        # Login as user with permission
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

    async def test_get_privmsg_details_and_mark_viewed(self, client: AsyncClient, db_session: AsyncSession):
        """Recipient should be able to fetch a single privmsg and it should be marked viewed."""
        # Create sender and recipient
        sender = Users(
            username="pm_sender",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_sender@example.com",
            active=1,
        )
        recipient = Users(
            username="pm_recipient",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_recipient@example.com",
            active=1,
        )
        db_session.add(sender)
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(sender)
        await db_session.refresh(recipient)

        # Create message from sender to recipient (unviewed)
        msg = Privmsgs(from_user_id=sender.user_id, to_user_id=recipient.user_id, text="Hello there", viewed=0)
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        # Login as recipient
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "pm_recipient", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Fetch single message
        response = await client.get(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["privmsg_id"] == msg.privmsg_id
        assert data["from_username"] == sender.username

        # Check DB was updated (viewed=1)
        await db_session.refresh(msg)
        assert msg.viewed == 1

    async def test_privmsg_handles_html_entities_in_text(self, client: AsyncClient, db_session: AsyncSession):
        """Ensure messages stored with HTML entities don't double-encode when rendered."""
        sender = Users(
            username="entity_sender",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="entity_sender@example.com",
            active=1,
        )
        recipient = Users(
            username="entity_recipient",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="entity_recipient@example.com",
            active=1,
        )
        db_session.add(sender)
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(sender)
        await db_session.refresh(recipient)

        problematic = 'So I want my title to be &quot;Alpha &amp; Omega&quot; or Alpha N&#039; Omega" if the &amp; character is illegal'
        msg = Privmsgs(from_user_id=sender.user_id, to_user_id=recipient.user_id, subject='i&#039;m sad', text=problematic, viewed=0)
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        # Login as recipient
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "entity_recipient", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.get(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        data = response.json()

        # The rendered HTML should not contain double-encoded &amp;quot; sequences
        assert "&amp;quot;" not in data["text_html"]
        # It should contain a properly encoded quote entity for HTML output
        assert "&quot;Alpha" in data["text_html"] or 'Alpha' in data["text_html"]

        # Subject should be normalized and should not contain HTML entity for apostrophe
        assert "&#039;" not in data["subject"]
        assert "'" in data["subject"]

    async def test_get_privmsg_details_sender_can_view_without_marking_viewed(self, client: AsyncClient, db_session: AsyncSession):
        """Sender should be able to fetch their sent message and it should not mark viewed."""
        sender = Users(
            username="pm_sender2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_sender2@example.com",
            active=1,
        )
        recipient = Users(
            username="pm_recipient2",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_recipient2@example.com",
            active=1,
        )
        db_session.add(sender)
        db_session.add(recipient)
        await db_session.commit()
        await db_session.refresh(sender)
        await db_session.refresh(recipient)

        msg = Privmsgs(from_user_id=sender.user_id, to_user_id=recipient.user_id, text="Hi there", viewed=0)
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        # Login as sender
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "pm_sender2", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.get(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        data = response.json()
        assert data["privmsg_id"] == msg.privmsg_id

        # Ensure viewed is still 0 in DB
        await db_session.refresh(msg)
        assert msg.viewed == 0

    async def test_get_privmsg_details_unauthorized(self, client: AsyncClient, db_session: AsyncSession):
        """A random third party should not be able to view someone else's message."""
        sender = Users(
            username="pm_sender3",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_sender3@example.com",
            active=1,
        )
        recipient = Users(
            username="pm_recipient3",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="pm_recipient3@example.com",
            active=1,
        )
        outsider = Users(
            username="outsider",
            password=get_password_hash("TestPassword123!"),
            password_type="bcrypt",
            salt="",
            email="outsider@example.com",
            active=1,
        )
        db_session.add(sender)
        db_session.add(recipient)
        db_session.add(outsider)
        await db_session.commit()
        await db_session.refresh(sender)
        await db_session.refresh(recipient)
        await db_session.refresh(outsider)

        msg = Privmsgs(from_user_id=sender.user_id, to_user_id=recipient.user_id, text="Secret", viewed=0)
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "outsider", "password": "TestPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        response = await client.get(f"/api/v1/privmsgs/{msg.privmsg_id}", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 403

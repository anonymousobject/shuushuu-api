"""Tests for privmsg thread endpoints."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.privmsg import Privmsgs
from app.models.user import Users


async def create_user(db: AsyncSession, username: str, **kwargs) -> Users:
    """Helper to create a test user."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
        **kwargs,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(client: AsyncClient, username: str) -> str:
    """Helper to login and return access token."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.api
class TestGetThreads:
    """Tests for GET /api/v1/privmsgs/threads endpoint."""

    async def test_list_threads(self, client: AsyncClient, db_session: AsyncSession):
        """Test listing conversation threads."""
        user_a = await create_user(db_session, "thread_a")
        user_b = await create_user(db_session, "thread_b")

        thread_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        for i in range(2):
            msg = Privmsgs(
                from_user_id=user_a.user_id if i == 0 else user_b.user_id,
                to_user_id=user_b.user_id if i == 0 else user_a.user_id,
                subject="Hello" if i == 0 else "Re: Hello",
                text=f"Message {i}",
                thread_id=thread_id,
                date=now + timedelta(minutes=i),
                viewed=1 if i == 0 else 0,
            )
            db_session.add(msg)
        await db_session.commit()

        token = await login(client, "thread_a")
        response = await client.get(
            "/api/v1/privmsgs/threads",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1

        thread = next(t for t in data["threads"] if t["thread_id"] == thread_id)
        assert thread["subject"] == "Hello"
        assert thread["other_user_id"] == user_b.user_id
        assert thread["other_username"] == "thread_b"
        assert thread["unread_count"] == 1
        assert thread["message_count"] == 2

    async def test_list_threads_unauthenticated(self, client: AsyncClient):
        """Test listing threads without authentication."""
        response = await client.get("/api/v1/privmsgs/threads")
        assert response.status_code == 401

    async def test_list_threads_filter_unread(self, client: AsyncClient, db_session: AsyncSession):
        """Test filtering threads to only unread."""
        user_a = await create_user(db_session, "unread_a")
        user_b = await create_user(db_session, "unread_b")
        user_c = await create_user(db_session, "unread_c")

        now = datetime.now(UTC)

        t1 = str(uuid.uuid4())
        msg1 = Privmsgs(
            from_user_id=user_b.user_id, to_user_id=user_a.user_id,
            subject="Read thread", text="Hi", thread_id=t1, date=now, viewed=1,
        )
        db_session.add(msg1)

        t2 = str(uuid.uuid4())
        msg2 = Privmsgs(
            from_user_id=user_c.user_id, to_user_id=user_a.user_id,
            subject="Unread thread", text="Hey", thread_id=t2, date=now, viewed=0,
        )
        db_session.add(msg2)
        await db_session.commit()

        token = await login(client, "unread_a")
        response = await client.get(
            "/api/v1/privmsgs/threads?filter=unread",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        thread_ids = [t["thread_id"] for t in data["threads"]]
        assert t2 in thread_ids
        assert t1 not in thread_ids

    async def test_threads_exclude_left_conversations(self, client: AsyncClient, db_session: AsyncSession):
        """Test that left (soft-deleted) conversations don't appear."""
        user_a = await create_user(db_session, "left_a")
        user_b = await create_user(db_session, "left_b")

        thread_id = str(uuid.uuid4())
        msg = Privmsgs(
            from_user_id=user_b.user_id, to_user_id=user_a.user_id,
            subject="Left thread", text="Bye", thread_id=thread_id,
            to_del=1,
        )
        db_session.add(msg)
        await db_session.commit()

        token = await login(client, "left_a")
        response = await client.get(
            "/api/v1/privmsgs/threads",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        thread_ids = [t["thread_id"] for t in response.json()["threads"]]
        assert thread_id not in thread_ids

    async def test_threads_sorted_by_latest_message(self, client: AsyncClient, db_session: AsyncSession):
        """Test threads are sorted by most recent message date."""
        user_a = await create_user(db_session, "sort_a")
        user_b = await create_user(db_session, "sort_b")
        user_c = await create_user(db_session, "sort_c")

        now = datetime.now(UTC)

        t1 = str(uuid.uuid4())
        db_session.add(Privmsgs(
            from_user_id=user_b.user_id, to_user_id=user_a.user_id,
            subject="Old", text="Old msg", thread_id=t1, date=now - timedelta(hours=1),
        ))

        t2 = str(uuid.uuid4())
        db_session.add(Privmsgs(
            from_user_id=user_c.user_id, to_user_id=user_a.user_id,
            subject="New", text="New msg", thread_id=t2, date=now,
        ))
        await db_session.commit()

        token = await login(client, "sort_a")
        response = await client.get(
            "/api/v1/privmsgs/threads",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        threads = response.json()["threads"]
        thread_ids = [t["thread_id"] for t in threads]
        assert thread_ids.index(t2) < thread_ids.index(t1)

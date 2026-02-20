"""Tests for news API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.core.security import create_access_token
from app.models.news import News
from app.models.permissions import Perms, UserPerms
from app.models.user import Users


@pytest.fixture
async def news_item(db_session: AsyncSession) -> News:
    """Create a test news item owned by user_id=1."""
    item = News(
        user_id=1,
        title="Test News",
        news_text="Test news content",
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def _user_with_permission(
    db_session: AsyncSession, permission: Permission
) -> tuple[Users, str]:
    """Create user_id=2 with a given permission and return (user, token)."""
    user = await db_session.get(Users, 2)
    user.active = 1
    perm = Perms(title=permission.value, desc=permission.description)
    db_session.add(perm)
    await db_session.flush()  # Get auto-generated perm_id
    user_perm = UserPerms(user_id=user.user_id, perm_id=perm.perm_id, permvalue=1)
    db_session.add(user_perm)
    await db_session.commit()
    token = create_access_token(user.user_id)
    return user, token


@pytest.fixture
async def user_with_news_create(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_CREATE permission and return (user, token)."""
    return await _user_with_permission(db_session, Permission.NEWS_CREATE)


@pytest.fixture
async def user_with_news_edit(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_EDIT permission and return (user, token)."""
    return await _user_with_permission(db_session, Permission.NEWS_EDIT)


@pytest.fixture
async def user_with_news_delete(db_session: AsyncSession) -> tuple[Users, str]:
    """Create a user with NEWS_DELETE permission and return (user, token)."""
    return await _user_with_permission(db_session, Permission.NEWS_DELETE)


@pytest.fixture
async def unprivileged_token(db_session: AsyncSession) -> str:
    """Token for an authenticated user with no news permissions."""
    user = await db_session.get(Users, 3)
    user.active = 1
    await db_session.commit()
    return create_access_token(user.user_id)


class TestListNews:
    """GET /api/v1/news"""

    async def test_list_empty(self, client: AsyncClient):
        """List returns empty result when no news exists."""
        response = await client.get("/api/v1/news")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["news"] == []

    async def test_list_returns_news_with_username(
        self, client: AsyncClient, news_item: News
    ):
        """List returns news items with username from user join."""
        response = await client.get("/api/v1/news")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["news"][0]["news_id"] == news_item.news_id
        assert data["news"][0]["username"] == "testuser"  # user_id=1
        assert data["news"][0]["title"] == "Test News"

    async def test_list_pagination(self, client: AsyncClient, db_session: AsyncSession):
        """List respects pagination parameters."""
        for i in range(3):
            db_session.add(News(user_id=1, title=f"News {i}", news_text=f"Content {i}"))
        await db_session.commit()

        response = await client.get("/api/v1/news?page=1&per_page=2")
        data = response.json()
        assert data["total"] == 3
        assert len(data["news"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2

    async def test_list_ordered_newest_first(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """List returns news ordered by news_id DESC (newest first)."""
        for i in range(3):
            db_session.add(News(user_id=1, title=f"News {i}", news_text=f"Content {i}"))
        await db_session.commit()

        response = await client.get("/api/v1/news")
        data = response.json()
        ids = [item["news_id"] for item in data["news"]]
        assert ids == sorted(ids, reverse=True)


class TestGetNews:
    """GET /api/v1/news/{news_id}"""

    async def test_get_existing(self, client: AsyncClient, news_item: News):
        """Get returns a single news item with username."""
        response = await client.get(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["news_id"] == news_item.news_id
        assert data["username"] == "testuser"

    async def test_get_not_found(self, client: AsyncClient):
        """Get returns 404 for non-existent news_id."""
        response = await client.get("/api/v1/news/99999")
        assert response.status_code == 404


class TestCreateNews:
    """POST /api/v1/news"""

    async def test_create_requires_auth(self, client: AsyncClient):
        """Create returns 401 without authentication."""
        response = await client.post(
            "/api/v1/news", json={"title": "Test", "news_text": "Content"}
        )
        assert response.status_code == 401

    async def test_create_requires_permission(
        self, client: AsyncClient, unprivileged_token: str
    ):
        """Create returns 403 without NEWS_CREATE permission."""
        response = await client.post(
            "/api/v1/news",
            json={"title": "Test", "news_text": "Content"},
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_create_success(
        self, client: AsyncClient, user_with_news_create: tuple[Users, str]
    ):
        """Create returns 201 with valid data and permission."""
        user, token = user_with_news_create
        response = await client.post(
            "/api/v1/news",
            json={"title": "New Post", "news_text": "Post content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Post"
        assert data["news_text"] == "Post content"
        assert data["user_id"] == user.user_id
        assert data["username"] == "testuser2"
        assert data["edited"] is None

    async def test_create_validates_title_required(
        self, client: AsyncClient, user_with_news_create: tuple[Users, str]
    ):
        """Create returns 422 when title is missing."""
        _, token = user_with_news_create
        response = await client.post(
            "/api/v1/news",
            json={"news_text": "Content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422


class TestUpdateNews:
    """PUT /api/v1/news/{news_id}"""

    async def test_update_requires_auth(self, client: AsyncClient, news_item: News):
        """Update returns 401 without authentication."""
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}", json={"title": "Updated"}
        )
        assert response.status_code == 401

    async def test_update_requires_permission(
        self, client: AsyncClient, news_item: News, unprivileged_token: str
    ):
        """Update returns 403 without NEWS_EDIT permission."""
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"title": "Updated"},
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_update_success(
        self, client: AsyncClient, news_item: News, user_with_news_edit: tuple[Users, str]
    ):
        """Update returns 200 and sets edited timestamp."""
        _, token = user_with_news_edit
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"title": "Updated Title"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["news_text"] == "Test news content"  # unchanged
        assert data["edited"] is not None

    async def test_update_news_text_only(
        self, client: AsyncClient, news_item: News, user_with_news_edit: tuple[Users, str]
    ):
        """Update with only news_text preserves original title."""
        _, token = user_with_news_edit
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"news_text": "Updated content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test News"  # unchanged
        assert data["news_text"] == "Updated content"
        assert data["edited"] is not None

    async def test_update_both_fields(
        self, client: AsyncClient, news_item: News, user_with_news_edit: tuple[Users, str]
    ):
        """Update with both fields changes both."""
        _, token = user_with_news_edit
        response = await client.put(
            f"/api/v1/news/{news_item.news_id}",
            json={"title": "New Title", "news_text": "New content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Title"
        assert data["news_text"] == "New content"

    async def test_update_not_found(
        self, client: AsyncClient, user_with_news_edit: tuple[Users, str]
    ):
        """Update returns 404 for non-existent news_id."""
        _, token = user_with_news_edit
        response = await client.put(
            "/api/v1/news/99999",
            json={"title": "Updated"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


class TestDeleteNews:
    """DELETE /api/v1/news/{news_id}"""

    async def test_delete_requires_auth(self, client: AsyncClient, news_item: News):
        """Delete returns 401 without authentication."""
        response = await client.delete(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 401

    async def test_delete_requires_permission(
        self, client: AsyncClient, news_item: News, unprivileged_token: str
    ):
        """Delete returns 403 without NEWS_DELETE permission."""
        response = await client.delete(
            f"/api/v1/news/{news_item.news_id}",
            headers={"Authorization": f"Bearer {unprivileged_token}"},
        )
        assert response.status_code == 403

    async def test_delete_success(
        self,
        client: AsyncClient,
        news_item: News,
        user_with_news_delete: tuple[Users, str],
    ):
        """Delete returns 204 and removes the news item."""
        _, token = user_with_news_delete
        response = await client.delete(
            f"/api/v1/news/{news_item.news_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        # Confirm it's gone
        response = await client.get(f"/api/v1/news/{news_item.news_id}")
        assert response.status_code == 404

    async def test_delete_not_found(
        self, client: AsyncClient, user_with_news_delete: tuple[Users, str]
    ):
        """Delete returns 404 for non-existent news_id."""
        _, token = user_with_news_delete
        response = await client.delete(
            "/api/v1/news/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

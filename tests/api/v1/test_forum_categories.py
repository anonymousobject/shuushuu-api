"""Tests for forum category endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestListCategories:
    """GET /api/v1/forum/categories"""

    async def test_anon_sees_public_not_gated(
        self, client: AsyncClient, public_category, staff_category
    ):
        response = await client.get("/api/v1/forum/categories")
        assert response.status_code == 200
        titles = [c["title"] for c in response.json()["categories"]]
        assert "Site Discussion" in titles
        assert "Mod Board" not in titles

    async def test_plain_user_does_not_see_gated(
        self, client: AsyncClient, staff_category, tagger_category, user_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(user_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert titles == []

    async def test_tagger_sees_tagger_not_staff(
        self, client: AsyncClient, staff_category, tagger_category, tagger_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(tagger_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert "Tagger Board" in titles
        assert "Mod Board" not in titles

    async def test_staff_sees_everything(
        self, client: AsyncClient, public_category, staff_category, tagger_category, staff_token
    ):
        response = await client.get("/api/v1/forum/categories", headers=_auth(staff_token))
        titles = [c["title"] for c in response.json()["categories"]]
        assert set(titles) == {"Site Discussion", "Mod Board", "Tagger Board"}

    async def test_ordered_by_sort_order(
        self, client: AsyncClient, public_category, announce_category
    ):
        response = await client.get("/api/v1/forum/categories")
        titles = [c["title"] for c in response.json()["categories"]]
        assert titles == ["Announcements", "Site Discussion"]  # sort_order 0 before 1

    async def test_stats_and_last_post(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        thread = await make_thread(db_session, public_category, title="Latest thread")
        response = await client.get("/api/v1/forum/categories")
        cat = response.json()["categories"][0]
        assert cat["thread_count"] == 1
        assert cat["post_count"] == 1
        assert cat["last_thread_id"] == thread.thread_id
        assert cat["last_thread_title"] == "Latest thread"
        assert cat["last_post_user"]["username"] == "testuser"

    async def test_capabilities(
        self, client: AsyncClient, announce_category, user_token, staff_token
    ):
        # Anon: no capabilities anywhere
        anon = (await client.get("/api/v1/forum/categories")).json()["categories"][0]
        assert anon["can_create_thread"] is False
        assert anon["can_reply"] is False
        # Plain user on Announcements: reply yes, create no
        plain = (
            await client.get("/api/v1/forum/categories", headers=_auth(user_token))
        ).json()["categories"][0]
        assert plain["can_create_thread"] is False
        assert plain["can_reply"] is True
        # Staff: both
        staff = (
            await client.get("/api/v1/forum/categories", headers=_auth(staff_token))
        ).json()["categories"][0]
        assert staff["can_create_thread"] is True
        assert staff["can_reply"] is True


class TestCreateCategory:
    """POST /api/v1/forum/categories"""

    async def test_requires_auth(self, client: AsyncClient):
        response = await client.post("/api/v1/forum/categories", json={"title": "New"})
        assert response.status_code == 401

    async def test_requires_permission(self, client: AsyncClient, user_token):
        response = await client.post(
            "/api/v1/forum/categories", json={"title": "New"}, headers=_auth(user_token)
        )
        assert response.status_code == 403

    async def test_create_success(self, client: AsyncClient, category_manager_token):
        response = await client.post(
            "/api/v1/forum/categories",
            json={
                "title": "Feature Requests",
                "description": "Ask for features",
                "sort_order": 5,
                "thread_create_perm": None,
                "view_perm": None,
                "reply_perm": None,
            },
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Feature Requests"
        assert data["sort_order"] == 5
        assert data["view_perm"] is None

    async def test_invalid_perm_rejected(self, client: AsyncClient, category_manager_token):
        response = await client.post(
            "/api/v1/forum/categories",
            json={"title": "Bad", "view_perm": "user_ban"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 422

    async def test_duplicate_title_conflict(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.post(
            "/api/v1/forum/categories",
            json={"title": "Site Discussion"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409


class TestUpdateCategory:
    """PATCH /api/v1/forum/categories/{category_id}"""

    async def test_requires_permission(self, client: AsyncClient, public_category, user_token):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Renamed"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_update_fields(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Renamed", "view_perm": "forum_access_staff"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Renamed"
        assert data["view_perm"] == "forum_access_staff"
        assert data["description"] == "General site talk"  # unchanged

    async def test_not_found(self, client: AsyncClient, category_manager_token):
        response = await client.patch(
            "/api/v1/forum/categories/99999",
            json={"title": "X"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 404

    async def test_duplicate_title_conflict(
        self, client: AsyncClient, public_category, announce_category, category_manager_token
    ):
        response = await client.patch(
            f"/api/v1/forum/categories/{public_category.category_id}",
            json={"title": "Announcements"},
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409


class TestDeleteCategory:
    """DELETE /api/v1/forum/categories/{category_id}"""

    async def test_delete_empty_category(
        self, client: AsyncClient, public_category, category_manager_token
    ):
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 204

    async def test_delete_nonempty_conflict(
        self, client: AsyncClient, db_session, public_category, category_manager_token
    ):
        thread = await make_thread(db_session, public_category)
        # Even a soft-deleted thread blocks deletion (FK RESTRICT)
        thread.deleted = True
        await db_session.commit()
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(category_manager_token),
        )
        assert response.status_code == 409

    async def test_requires_permission(self, client: AsyncClient, public_category, user_token):
        response = await client.delete(
            f"/api/v1/forum/categories/{public_category.category_id}",
            headers=_auth(user_token),
        )
        assert response.status_code == 403

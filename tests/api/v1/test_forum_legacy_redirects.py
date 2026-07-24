"""Tests for the legacy phpBB URL redirect resolvers."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestViewforumRedirect:
    """GET /api/v1/forum/legacy/viewforum"""

    async def test_redirects_to_category(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        public_category.legacy_forum_id = 7
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewforum?f=7")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/{public_category.category_id}"

    async def test_unknown_forum_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewforum?f=999999")
        assert r.status_code == 404

    async def test_gated_forum_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        staff_category.legacy_forum_id = 3
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewforum?f=3")
        assert r.status_code == 404

    async def test_gated_forum_visible_to_staff_301(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, staff_token
    ):
        staff_category.legacy_forum_id = 3
        await db_session.commit()
        r = await client.get(
            "/api/v1/forum/legacy/viewforum?f=3", headers=_auth(staff_token)
        )
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/{staff_category.category_id}"


class TestViewtopicRedirect:
    """GET /api/v1/forum/legacy/viewtopic"""

    async def test_redirects_to_thread(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        public_thread.legacy_topic_id = 2096
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?f=10&t=2096")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/threads/{public_thread.thread_id}"

    async def test_topic_id_alone_is_enough(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        public_thread.legacy_topic_id = 55
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=55")
        assert r.status_code == 301
        assert r.headers["location"] == f"/forum/threads/{public_thread.thread_id}"

    async def test_unknown_topic_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=999999")
        assert r.status_code == 404

    async def test_gated_topic_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        thread = await make_thread(db_session, staff_category, title="Secret")
        thread.legacy_topic_id = 42
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=42")
        assert r.status_code == 404

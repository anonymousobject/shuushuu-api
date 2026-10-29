"""Tests for the legacy phpBB URL redirect resolvers."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.v1.conftest import make_thread
from tests.api.v1.test_forum_permalink import _add_reply, _opening_post


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
        r = await client.get("/api/v1/forum/legacy/viewforum?f=3", headers=_auth(staff_token))
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

    async def test_missing_and_gated_topic_return_identical_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        # A gated-but-existing legacy topic and a missing one must be
        # indistinguishable to an anonymous caller (no existence oracle).
        thread = await make_thread(db_session, staff_category, title="Secret")
        thread.legacy_topic_id = 4242
        await db_session.commit()
        missing_resp = await client.get("/api/v1/forum/legacy/viewtopic?t=999999")
        gated_resp = await client.get("/api/v1/forum/legacy/viewtopic?t=4242")
        assert missing_resp.status_code == gated_resp.status_code == 404
        assert missing_resp.json() == gated_resp.json()

    async def test_post_id_alone_redirects_to_post_page(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        # phpBB's standard post link is viewtopic.php?p=N#pN, with no t.
        replies = [await _add_reply(db_session, public_thread, f"r{i}") for i in range(20)]
        replies[19].legacy_post_id = 777  # 21st post overall -> page 2
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?p=777")
        assert r.status_code == 301
        assert r.headers["location"] == (
            f"/forum/threads/{public_thread.thread_id}?page=2#post-{replies[19].post_id}"
        )

    async def test_post_id_takes_precedence_over_topic_id(
        self, client: AsyncClient, db_session: AsyncSession, public_category, public_thread
    ):
        other = await make_thread(db_session, public_category, title="Other")
        other.legacy_topic_id = 9
        opening = await _opening_post(db_session, public_thread)
        opening.legacy_post_id = 10
        await db_session.commit()
        r = await client.get("/api/v1/forum/legacy/viewtopic?t=9&p=10")
        assert r.status_code == 301
        assert r.headers["location"] == (
            f"/forum/threads/{public_thread.thread_id}?page=1#post-{opening.post_id}"
        )

    async def test_unknown_post_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewtopic?p=999999")
        assert r.status_code == 404

    async def test_neither_topic_nor_post_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/legacy/viewtopic")
        assert r.status_code == 404

    async def test_missing_and_gated_post_return_identical_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        thread = await make_thread(db_session, staff_category, title="Secret")
        opening = await _opening_post(db_session, thread)
        opening.legacy_post_id = 4343
        await db_session.commit()
        missing_resp = await client.get("/api/v1/forum/legacy/viewtopic?p=999999")
        gated_resp = await client.get("/api/v1/forum/legacy/viewtopic?p=4343")
        assert missing_resp.status_code == gated_resp.status_code == 404
        assert missing_resp.json() == gated_resp.json()


class TestViewtopicDeletedThread:
    """A soft-deleted thread is as invisible to non-moderators as a missing one."""

    async def test_topic_404_for_users_301_for_mods(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        public_category,
        user_token,
        staff_token,
    ):
        thread = await make_thread(db_session, public_category, title="Removed")
        thread.legacy_topic_id = 55
        thread.deleted = True
        await db_session.commit()
        missing = await client.get("/api/v1/forum/legacy/viewtopic?t=999999")
        anon = await client.get("/api/v1/forum/legacy/viewtopic?t=55")
        user = await client.get("/api/v1/forum/legacy/viewtopic?t=55", headers=_auth(user_token))
        assert missing.status_code == anon.status_code == user.status_code == 404
        assert missing.json() == anon.json() == user.json()
        mod = await client.get("/api/v1/forum/legacy/viewtopic?t=55", headers=_auth(staff_token))
        assert mod.status_code == 301

    async def test_post_in_deleted_thread_404_for_users_301_for_mods(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        public_category,
        user_token,
        staff_token,
    ):
        thread = await make_thread(db_session, public_category, title="Removed")
        opening = await _opening_post(db_session, thread)
        opening.legacy_post_id = 5656
        thread.deleted = True
        await db_session.commit()
        missing = await client.get(
            "/api/v1/forum/legacy/viewtopic?p=999999", headers=_auth(user_token)
        )
        user = await client.get("/api/v1/forum/legacy/viewtopic?p=5656", headers=_auth(user_token))
        assert missing.status_code == user.status_code == 404
        assert missing.json() == user.json()
        mod = await client.get("/api/v1/forum/legacy/viewtopic?p=5656", headers=_auth(staff_token))
        assert mod.status_code == 301

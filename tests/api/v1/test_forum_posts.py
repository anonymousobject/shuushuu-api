"""Tests for forum post endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.forum import ForumCategories, ForumThreads
from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _get_thread(db_session: AsyncSession, thread_id: int) -> ForumThreads:
    db_session.expire_all()
    thread = await db_session.get(ForumThreads, thread_id)
    assert thread is not None
    return thread


class TestCreatePost:
    """POST /api/v1/forum/threads/{thread_id}/posts"""

    async def test_requires_auth(self, client: AsyncClient, public_thread):
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
        )
        assert response.status_code == 401

    async def test_reply_updates_thread_stats(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": 'Nice thread [quote="testuser"]Opening post[/quote]'},
            headers=_auth(user_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user"]["username"] == "testuser3"
        assert "blockquote" in data["post_text_html"]

        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 3
        assert thread.last_post_at is not None

    async def test_own_reply_not_unread_for_author(
        self, client: AsyncClient, public_category, public_thread, user_token
    ):
        await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "my reply"},
            headers=_auth(user_token),
        )
        listed = (
            await client.get(
                f"/api/v1/forum/categories/{public_category.category_id}/threads",
                headers=_auth(user_token),
            )
        ).json()["threads"][0]
        assert listed["unread"] is False

    async def test_locked_thread_403(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        public_thread.locked = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403
        assert "locked" in response.json()["detail"].lower()

    async def test_locked_blocks_moderators_too(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        public_thread.locked = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 403

    async def test_deleted_thread_403_for_moderator(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        # A moderator sees the deleted thread exists (403 "restore first"),
        # rather than a 404 — parity with get_thread's moderator visibility.
        public_thread.deleted = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 403

    async def test_deleted_thread_404_for_non_moderator(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        # Non-moderators must not be able to tell a deleted thread apart from a
        # missing one via the reply endpoint (no existence leak).
        public_thread.deleted = True
        await db_session.commit()
        response = await client.post(
            f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_view_gated_thread_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        thread = await make_thread(db_session, staff_category)
        response = await client.post(
            f"/api/v1/forum/threads/{thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_reply_gated_403(
        self, client: AsyncClient, db_session: AsyncSession, user_token
    ):
        # Public view, staff-only replies (a read-only announcements pattern)
        cat = ForumCategories(
            title="Read Only",
            reply_perm=Permission.FORUM_ACCESS_STAFF.value,
        )
        db_session.add(cat)
        await db_session.flush()
        thread = await make_thread(db_session, cat)
        response = await client.post(
            f"/api/v1/forum/threads/{thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403


class TestUpdatePost:
    """PATCH /api/v1/forum/posts/{post_id}"""

    async def _create_reply(self, client, thread_id: int, token: str) -> dict:
        response = await client.post(
            f"/api/v1/forum/threads/{thread_id}/posts",
            json={"post_text": "original"},
            headers=_auth(token),
        )
        assert response.status_code == 201
        return response.json()

    async def test_owner_edits_with_tracking(
        self, client: AsyncClient, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "edited"},
            headers=_auth(user_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["post_text"] == "edited"
        assert data["update_count"] == 1
        assert data["last_updated"] is not None
        assert data["last_updated_user_id"] == 3

    async def test_non_owner_cannot_edit(
        self, client: AsyncClient, public_thread, user_token, author_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "hijack"},
            headers=_auth(author_token),
        )
        assert response.status_code == 403

    async def test_moderator_edits_others_post(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "moderated"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["last_updated_user_id"] == 2

    async def test_deleted_post_edit_400(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(user_token)
        )
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "necro-edit"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 400

    async def test_plain_user_cannot_set_deleted(
        self, client: AsyncClient, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"deleted": True},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_moderator_restores_post_and_stats_recover(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(staff_token)
        )
        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 1

        response = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"deleted": False},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 3

    async def test_locked_thread_blocks_owner_edit_but_not_moderator(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        # The lock must block the post owner's own edit (parity with create_post),
        # but a moderator may still act (they unlock first in the normal flow).
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        thread = await _get_thread(db_session, public_thread.thread_id)
        thread.locked = True
        await db_session.commit()

        owner_resp = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "sneaky edit"},
            headers=_auth(user_token),
        )
        assert owner_resp.status_code == 403
        assert "locked" in owner_resp.json()["detail"].lower()

        mod_resp = await client.patch(
            f"/api/v1/forum/posts/{post['post_id']}",
            json={"post_text": "mod edit"},
            headers=_auth(staff_token),
        )
        assert mod_resp.status_code == 200
        assert mod_resp.json()["post_text"] == "mod edit"


class TestDeletePost:
    """DELETE /api/v1/forum/posts/{post_id}"""

    async def _create_reply(self, client, thread_id: int, token: str) -> dict:
        response = await client.post(
            f"/api/v1/forum/threads/{thread_id}/posts",
            json={"post_text": "to delete"},
            headers=_auth(token),
        )
        return response.json()

    async def test_owner_deletes_and_stats_recompute(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(user_token)
        )
        assert response.status_code == 204

        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 1
        assert thread.last_post_user_id == 1  # back to the opening post's author

        # Tombstone visible in the thread, text blanked
        detail = (
            await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        ).json()
        assert detail["posts"][1]["deleted"] is True
        assert detail["posts"][1]["post_text"] == ""

    async def test_opening_post_cannot_be_deleted(
        self, client: AsyncClient, public_thread, author_token
    ):
        detail = (
            await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        ).json()
        opening_id = detail["posts"][0]["post_id"]
        response = await client.delete(
            f"/api/v1/forum/posts/{opening_id}", headers=_auth(author_token)
        )
        assert response.status_code == 400

    async def test_non_owner_cannot_delete(
        self, client: AsyncClient, public_thread, user_token, author_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(author_token)
        )
        assert response.status_code == 403

    async def test_moderator_deletes_others_post(
        self, client: AsyncClient, public_thread, user_token, staff_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        response = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(staff_token)
        )
        assert response.status_code == 204

    async def test_locked_thread_blocks_owner_delete_but_not_moderator(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        # A locked thread must block the owner's soft-delete of their own post,
        # but a moderator may still remove it.
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        thread = await _get_thread(db_session, public_thread.thread_id)
        thread.locked = True
        await db_session.commit()

        owner_resp = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(user_token)
        )
        assert owner_resp.status_code == 403
        assert "locked" in owner_resp.json()["detail"].lower()

        mod_resp = await client.delete(
            f"/api/v1/forum/posts/{post['post_id']}", headers=_auth(staff_token)
        )
        assert mod_resp.status_code == 204

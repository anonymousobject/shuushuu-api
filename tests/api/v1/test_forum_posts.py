"""Tests for forum post endpoints."""

from unittest.mock import patch

import pymysql
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.forum import ForumCategories, ForumThreads
from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _db_error(errno: int, message: str) -> OperationalError:
    """Build the sqlalchemy error the aiomysql/pymysql driver raises for `errno`."""
    return OperationalError(
        "SELECT ... FOR UPDATE", None, pymysql.err.OperationalError(errno, message)
    )


def _snapshot_conflict_error() -> OperationalError:
    """The error MariaDB raises under innodb_snapshot_isolation (ER_CHECKREAD)."""
    return _db_error(1020, "Record has changed since last read in table 'forum_threads'")


def _flaky_locking_read(fail_times: int, error: OperationalError):
    """Patch AsyncSession.execute to raise `error` the first `fail_times` times
    it runs a locking (FOR UPDATE) statement — the exact site MariaDB aborts
    with ER_CHECKREAD (1020) under innodb_snapshot_isolation. Non-locking
    statements (and db.get, which does not go through AsyncSession.execute) pass
    through untouched. Returns (patch_ctx, calls) where calls records each
    intercepted locking read."""
    real_execute = AsyncSession.execute
    calls: list[int] = []

    async def execute(self, statement, *args, **kwargs):
        if getattr(statement, "_for_update_arg", None) is not None:
            calls.append(1)
            if len(calls) <= fail_times:
                raise error
        return await real_execute(self, statement, *args, **kwargs)

    return patch.object(AsyncSession, "execute", execute), calls


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

    async def test_missing_and_gated_thread_reply_return_identical_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        # Replying to a gated-but-existing thread and to a missing one must be
        # indistinguishable (no existence oracle), matching get_thread.
        thread = await make_thread(db_session, staff_category)
        missing_resp = await client.post(
            "/api/v1/forum/threads/999999/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        gated_resp = await client.post(
            f"/api/v1/forum/threads/{thread.thread_id}/posts",
            json={"post_text": "hi"},
            headers=_auth(user_token),
        )
        assert missing_resp.status_code == gated_resp.status_code == 404
        assert missing_resp.json() == gated_resp.json()

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


class TestForumSnapshotConflictRetry:
    """create_post/update_post/delete_post lock the thread row (FOR UPDATE) and
    recompute denormalized stats. Under innodb_snapshot_isolation a concurrent
    commit to the same thread/post rows aborts that locking read with
    ER_CHECKREAD (errno 1020). Each write path must retry on a fresh snapshot
    instead of surfacing a 500. The 1020 is injected into the locking read
    itself — the real conflict site — via _flaky_locking_read, exercising the
    real retry helper (app/core/db_retry.py).

    needs_commit: the retry performs a real session rollback to obtain a fresh
    snapshot; under the default SAVEPOINT test isolation that rollback would
    unwind the committed thread/user fixtures too, which cannot happen in
    production where they are durably committed."""

    async def _create_reply(self, client: AsyncClient, thread_id: int, token: str) -> dict:
        r = await client.post(
            f"/api/v1/forum/threads/{thread_id}/posts",
            json={"post_text": "original"},
            headers=_auth(token),
        )
        assert r.status_code == 201
        return r.json()

    @pytest.mark.needs_commit
    async def test_reply_retries_snapshot_conflict_and_succeeds(
        self, client: AsyncClient, public_thread, user_token
    ):
        read_patch, calls = _flaky_locking_read(1, _snapshot_conflict_error())
        with read_patch:
            response = await client.post(
                f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
                json={"post_text": "hi"},
                headers=_auth(user_token),
            )
        assert response.status_code == 201, response.text
        assert len(calls) >= 2  # failed attempt + successful retry

        # The reply landed exactly once despite the retry.
        detail = (
            await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        ).json()
        assert detail["total"] == 2  # opening + the single retried reply

    @pytest.mark.needs_commit
    async def test_reply_gives_up_after_bounded_retries(
        self, client: AsyncClient, public_thread, user_token
    ):
        read_patch, calls = _flaky_locking_read(100, _snapshot_conflict_error())
        with read_patch, pytest.raises(OperationalError):
            await client.post(
                f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
                json={"post_text": "hi"},
                headers=_auth(user_token),
            )
        assert len(calls) == 3  # bounded: no infinite retry loop

    async def test_reply_does_not_retry_other_db_errors(
        self, client: AsyncClient, public_thread, user_token
    ):
        read_patch, calls = _flaky_locking_read(100, _db_error(1213, "Deadlock found"))
        with read_patch, pytest.raises(OperationalError):
            await client.post(
                f"/api/v1/forum/threads/{public_thread.thread_id}/posts",
                json={"post_text": "hi"},
                headers=_auth(user_token),
            )
        assert len(calls) == 1  # non-1020 error is not retried

    @pytest.mark.needs_commit
    async def test_edit_post_retries_snapshot_conflict_and_succeeds(
        self, client: AsyncClient, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        read_patch, calls = _flaky_locking_read(1, _snapshot_conflict_error())
        with read_patch:
            response = await client.patch(
                f"/api/v1/forum/posts/{post['post_id']}",
                json={"post_text": "edited"},
                headers=_auth(user_token),
            )
        assert response.status_code == 200, response.text
        assert response.json()["post_text"] == "edited"
        assert len(calls) >= 2

    @pytest.mark.needs_commit
    async def test_delete_post_retries_snapshot_conflict_and_succeeds(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token
    ):
        post = await self._create_reply(client, public_thread.thread_id, user_token)
        read_patch, calls = _flaky_locking_read(1, _snapshot_conflict_error())
        with read_patch:
            response = await client.delete(
                f"/api/v1/forum/posts/{post['post_id']}",
                headers=_auth(user_token),
            )
        assert response.status_code == 204, response.text
        assert len(calls) >= 2

        thread = await _get_thread(db_session, public_thread.thread_id)
        assert thread.post_count == 1  # stats recomputed exactly once

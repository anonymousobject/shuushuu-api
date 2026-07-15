"""Tests for forum thread endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts
from tests.api.v1.conftest import make_thread


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _add_reply(db_session, thread, user_id=2, text="A reply") -> ForumPosts:
    """Insert a reply directly and fix up denormalized fields (test setup only)."""
    post = ForumPosts(thread_id=thread.thread_id, user_id=user_id, post_text=text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    thread.post_count += 1
    thread.last_post_at = post.date
    thread.last_post_user_id = user_id
    await db_session.commit()
    return post


class TestListThreads:
    """GET /api/v1/forum/categories/{category_id}/threads"""

    async def test_lists_threads_with_envelope(
        self, client: AsyncClient, public_category, public_thread
    ):
        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["threads"][0]["title"] == "Test thread"
        assert data["threads"][0]["user"]["username"] == "testuser"
        assert data["threads"][0]["post_count"] == 1

    async def test_gated_category_404(self, client: AsyncClient, staff_category, user_token):
        response = await client.get(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_pinned_first_then_activity(
        self, client: AsyncClient, db_session: AsyncSession, public_category
    ):
        oldest = await make_thread(db_session, public_category, title="Oldest")
        await make_thread(db_session, public_category, title="Middle")
        await make_thread(db_session, public_category, title="Newest")
        oldest.pinned = True
        await db_session.commit()

        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        titles = [t["title"] for t in response.json()["threads"]]
        assert titles[0] == "Oldest"  # pinned wins over recency
        assert titles[1] == "Newest"

    async def test_excludes_deleted(
        self, client: AsyncClient, db_session: AsyncSession, public_category, public_thread
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.get(
            f"/api/v1/forum/categories/{public_category.category_id}/threads"
        )
        assert response.json()["total"] == 0

    async def test_unread_lifecycle(
        self, client: AsyncClient, db_session: AsyncSession, public_category, public_thread, user_token
    ):
        url = f"/api/v1/forum/categories/{public_category.category_id}/threads"
        # Anonymous: never unread
        anon = (await client.get(url)).json()["threads"][0]
        assert anon["unread"] is False
        # Fresh user: unread
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is True
        # Viewing the thread marks it read
        await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is False
        # A new reply by someone else makes it unread again
        await _add_reply(db_session, public_thread, user_id=2)
        listed = (await client.get(url, headers=_auth(user_token))).json()["threads"][0]
        assert listed["unread"] is True


class TestCreateThread:
    """POST /api/v1/forum/categories/{category_id}/threads"""

    async def test_requires_auth(self, client: AsyncClient, public_category):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
        )
        assert response.status_code == 401

    async def test_create_success(self, client: AsyncClient, public_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "My thread", "post_text": "Opening **post**"},
            headers=_auth(user_token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "My thread"
        assert data["post_count"] == 1
        assert data["user"]["username"] == "testuser3"
        assert data["unread"] is False  # own post never unread for the author

        # The opening post exists and renders markdown
        detail = (
            await client.get(f"/api/v1/forum/threads/{data['thread_id']}")
        ).json()
        assert detail["total"] == 1
        assert "<strong>post</strong>" in detail["posts"][0]["post_text_html"]

    async def test_create_gated_403(self, client: AsyncClient, announce_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{announce_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_view_gated_404_not_403(
        self, client: AsyncClient, staff_category, user_token
    ):
        response = await client.post(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            json={"title": "T", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 404

    async def test_staff_can_create_in_gated(
        self, client: AsyncClient, staff_category, staff_token
    ):
        response = await client.post(
            f"/api/v1/forum/categories/{staff_category.category_id}/threads",
            json={"title": "Staff only", "post_text": "body"},
            headers=_auth(staff_token),
        )
        assert response.status_code == 201

    async def test_empty_title_422(self, client: AsyncClient, public_category, user_token):
        response = await client.post(
            f"/api/v1/forum/categories/{public_category.category_id}/threads",
            json={"title": "", "post_text": "body"},
            headers=_auth(user_token),
        )
        assert response.status_code == 422


class TestGetThread:
    """GET /api/v1/forum/threads/{thread_id}"""

    async def test_anon_reads_public_thread(self, client: AsyncClient, public_thread):
        response = await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["thread"]["title"] == "Test thread"
        assert data["can_reply"] is False
        assert data["can_moderate"] is False
        assert data["total"] == 1
        assert data["posts"][0]["post_text"] == "Opening post"

    async def test_gated_thread_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        thread = await make_thread(db_session, staff_category)
        response = await client.get(
            f"/api/v1/forum/threads/{thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 404

    async def test_missing_and_gated_thread_return_identical_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        # An anonymous caller must not distinguish a gated-but-existing thread
        # from a missing one: both 404s must be byte-identical (no existence
        # oracle for enumerating gated thread ids).
        gated = await make_thread(db_session, staff_category, title="Secret")
        missing_resp = await client.get("/api/v1/forum/threads/999999")
        gated_resp = await client.get(f"/api/v1/forum/threads/{gated.thread_id}")
        assert missing_resp.status_code == gated_resp.status_code == 404
        assert missing_resp.json() == gated_resp.json()

    async def test_missing_and_gated_thread_mutation_return_identical_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category, user_token
    ):
        # The same non-existence-oracle guarantee must hold on the authenticated
        # mutation endpoints: PATCH/DELETE of a gated-but-existing thread must be
        # byte-identical to that of a missing thread (no enumeration of gated ids).
        gated = await make_thread(db_session, staff_category, title="Secret")
        for method, kwargs in (("patch", {"json": {"title": "x"}}), ("delete", {})):
            req = getattr(client, method)
            missing = await req(
                "/api/v1/forum/threads/999999", headers=_auth(user_token), **kwargs
            )
            gated_resp = await req(
                f"/api/v1/forum/threads/{gated.thread_id}", headers=_auth(user_token), **kwargs
            )
            assert missing.status_code == gated_resp.status_code == 404
            assert missing.json() == gated_resp.json()

    async def test_deleted_thread_404_for_users_200_for_mods(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, user_token, staff_token
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 404
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(staff_token)
        )
        assert response.status_code == 200
        assert response.json()["thread"]["deleted"] is True

    async def test_post_pagination(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        for i in range(3):
            await _add_reply(db_session, public_thread, text=f"reply {i}")
        response = await client.get(
            f"/api/v1/forum/threads/{public_thread.thread_id}?page=2&per_page=2"
        )
        data = response.json()
        assert data["total"] == 4  # opening + 3 replies
        assert len(data["posts"]) == 2
        assert data["posts"][0]["post_text"] == "reply 1"  # chronological order

    async def test_tombstone_hides_text_from_users_not_mods(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        reply = await _add_reply(db_session, public_thread, text="secret")
        reply.deleted = True
        await db_session.commit()

        url = f"/api/v1/forum/threads/{public_thread.thread_id}"
        anon_posts = (await client.get(url)).json()["posts"]
        assert anon_posts[1]["deleted"] is True
        assert anon_posts[1]["post_text"] == ""
        assert anon_posts[1]["post_text_html"] == ""

        mod_posts = (await client.get(url, headers=_auth(staff_token))).json()["posts"]
        assert mod_posts[1]["post_text"] == "secret"


class TestUpdateThread:
    """PATCH /api/v1/forum/threads/{thread_id}"""

    async def test_author_edits_title(self, client: AsyncClient, public_thread, author_token):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Renamed"},
            headers=_auth(author_token),
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"

    async def test_non_author_cannot_edit_title(
        self, client: AsyncClient, public_thread, user_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Hijacked"},
            headers=_auth(user_token),
        )
        assert response.status_code == 403

    async def test_mod_fields_require_moderate(
        self, client: AsyncClient, public_thread, author_token
    ):
        # Even the author cannot pin/lock without FORUM_MODERATE
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"pinned": True},
            headers=_auth(author_token),
        )
        assert response.status_code == 403

    async def test_moderator_pins_locks(self, client: AsyncClient, public_thread, staff_token):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"pinned": True, "locked": True},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pinned"] is True
        assert data["locked"] is True

    async def test_locked_thread_blocks_author_title_edit_but_not_moderator(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, author_token, staff_token
    ):
        # A lock must block the author's title rename too — otherwise the thread
        # author could still mutate a locked thread. Moderators may still rename.
        public_thread.locked = True
        await db_session.commit()

        author_resp = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Sneaky rename"},
            headers=_auth(author_token),
        )
        assert author_resp.status_code == 403
        assert "locked" in author_resp.json()["detail"].lower()

        mod_resp = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"title": "Mod rename"},
            headers=_auth(staff_token),
        )
        assert mod_resp.status_code == 200
        assert mod_resp.json()["title"] == "Mod rename"

    async def test_moderator_moves_thread(
        self, client: AsyncClient, public_thread, announce_category, staff_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"category_id": announce_category.category_id},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["category_id"] == announce_category.category_id

    async def test_move_to_missing_category_400(
        self, client: AsyncClient, public_thread, staff_token
    ):
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"category_id": 99999},
            headers=_auth(staff_token),
        )
        assert response.status_code == 400

    async def test_moderator_restores_deleted_thread(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        public_thread.deleted = True
        await db_session.commit()
        response = await client.patch(
            f"/api/v1/forum/threads/{public_thread.thread_id}",
            json={"deleted": False},
            headers=_auth(staff_token),
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is False


class TestDeleteThread:
    """DELETE /api/v1/forum/threads/{thread_id}"""

    async def test_author_deletes_replyless_thread(
        self, client: AsyncClient, public_thread, author_token
    ):
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(author_token)
        )
        assert response.status_code == 204
        response = await client.get(f"/api/v1/forum/threads/{public_thread.thread_id}")
        assert response.status_code == 404

    async def test_author_cannot_delete_thread_with_replies(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, author_token
    ):
        await _add_reply(db_session, public_thread)
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(author_token)
        )
        assert response.status_code == 403

    async def test_moderator_deletes_thread_with_replies(
        self, client: AsyncClient, db_session: AsyncSession, public_thread, staff_token
    ):
        await _add_reply(db_session, public_thread)
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(staff_token)
        )
        assert response.status_code == 204

    async def test_non_author_cannot_delete(
        self, client: AsyncClient, public_thread, user_token
    ):
        response = await client.delete(
            f"/api/v1/forum/threads/{public_thread.thread_id}", headers=_auth(user_token)
        )
        assert response.status_code == 403

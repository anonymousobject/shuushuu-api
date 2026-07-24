"""Tests for the forum post permalink resolver."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts, ForumThreads
from tests.api.v1.conftest import make_thread


async def _opening_post(db_session: AsyncSession, thread: ForumThreads) -> ForumPosts:
    result = await db_session.execute(
        select(ForumPosts)
        .where(ForumPosts.thread_id == thread.thread_id)  # type: ignore[arg-type]
        .order_by(ForumPosts.post_id)  # type: ignore[arg-type]
    )
    return result.scalars().first()


async def _add_reply(
    db_session: AsyncSession, thread: ForumThreads, text: str
) -> ForumPosts:
    post = ForumPosts(thread_id=thread.thread_id, user_id=1, post_text=text)
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)
    return post


class TestPostPermalink:
    """GET /api/v1/forum/posts/{post_id}/redirect"""

    async def test_opening_post_page_1(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        opening = await _opening_post(db_session, public_thread)
        r = await client.get(f"/api/v1/forum/posts/{opening.post_id}/redirect")
        assert r.status_code == 301
        assert (
            r.headers["location"]
            == f"/forum/threads/{public_thread.thread_id}?page=1#post-{opening.post_id}"
        )

    async def test_page_boundary(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        # Opening post is #1 (rank 0). Add 20 replies -> 21 posts total.
        replies = [await _add_reply(db_session, public_thread, f"r{i}") for i in range(20)]
        await db_session.commit()
        # replies[18] is the 20th post overall (rank 19) -> page 1.
        # replies[19] is the 21st post overall (rank 20) -> page 2.
        r20 = await client.get(f"/api/v1/forum/posts/{replies[18].post_id}/redirect")
        assert r20.headers["location"].endswith(f"?page=1#post-{replies[18].post_id}")
        r21 = await client.get(f"/api/v1/forum/posts/{replies[19].post_id}/redirect")
        assert r21.headers["location"].endswith(f"?page=2#post-{replies[19].post_id}")

    async def test_unknown_post_404(self, client: AsyncClient):
        r = await client.get("/api/v1/forum/posts/999999/redirect")
        assert r.status_code == 404

    async def test_deleted_post_still_resolves(
        self, client: AsyncClient, db_session: AsyncSession, public_thread
    ):
        reply = await _add_reply(db_session, public_thread, "doomed")
        reply.deleted = True
        await db_session.commit()
        r = await client.get(f"/api/v1/forum/posts/{reply.post_id}/redirect")
        assert r.status_code == 301
        assert f"#post-{reply.post_id}" in r.headers["location"]

    async def test_gated_thread_post_hidden_from_anon_404(
        self, client: AsyncClient, db_session: AsyncSession, staff_category
    ):
        thread = await make_thread(db_session, staff_category, title="Secret")
        opening = await _opening_post(db_session, thread)
        r = await client.get(f"/api/v1/forum/posts/{opening.post_id}/redirect")
        assert r.status_code == 404

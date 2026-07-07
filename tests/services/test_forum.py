"""Forum service helper tests."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads
from app.services.forum import can_access, recompute_thread_stats, upsert_thread_read


class TestCanAccess:
    def test_null_perm_is_open(self):
        assert can_access(set(), None) is True

    def test_missing_perm_denied(self):
        assert can_access(set(), "forum_access_staff") is False

    def test_present_perm_allowed(self):
        assert can_access({"forum_access_staff"}, "forum_access_staff") is True


async def _make_thread_with_posts(db_session: AsyncSession) -> ForumThreads:
    cat = ForumCategories(title="Svc Category")
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="t", user_id=1)
    db_session.add(thread)
    await db_session.flush()
    for uid in (1, 2, 3):
        db_session.add(ForumPosts(thread_id=thread.thread_id, user_id=uid, post_text=f"p{uid}"))
    await db_session.flush()
    return thread


class TestRecomputeThreadStats:
    async def test_counts_live_posts(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        await recompute_thread_stats(db_session, thread)
        assert thread.post_count == 3
        assert thread.last_post_user_id == 3  # newest post
        assert thread.last_post_at is not None

    async def test_ignores_soft_deleted_posts(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        # Soft-delete the newest post (user 3's)
        newest = (
            await db_session.execute(
                select(ForumPosts)
                .where(ForumPosts.thread_id == thread.thread_id)
                .order_by(ForumPosts.post_id.desc())
            )
        ).scalars().first()
        newest.deleted = True
        await db_session.flush()

        await recompute_thread_stats(db_session, thread)
        assert thread.post_count == 2
        assert thread.last_post_user_id == 2

    async def test_nulls_fields_when_all_posts_deleted(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        posts = (
            (
                await db_session.execute(
                    select(ForumPosts).where(ForumPosts.thread_id == thread.thread_id)
                )
            )
            .scalars()
            .all()
        )
        for post in posts:
            post.deleted = True
        await db_session.flush()

        await recompute_thread_stats(db_session, thread)
        assert thread.post_count == 0
        assert thread.last_post_at is None
        assert thread.last_post_user_id is None


class TestUpsertThreadRead:
    async def test_insert_then_update(self, db_session: AsyncSession):
        thread = await _make_thread_with_posts(db_session)
        thread_id = thread.thread_id  # Save before any expire
        t1 = datetime(2026, 7, 1, tzinfo=UTC)
        t2 = datetime(2026, 7, 2, tzinfo=UTC)

        await upsert_thread_read(db_session, 1, thread_id, t1)
        await db_session.commit()
        result = await db_session.execute(
            select(ForumThreadReads).where(
                ForumThreadReads.user_id == 1, ForumThreadReads.thread_id == thread_id
            )
        )
        row = result.scalars().first()
        assert row is not None

        await upsert_thread_read(db_session, 1, thread_id, t2)
        await db_session.commit()
        db_session.expire_all()
        result = await db_session.execute(
            select(ForumThreadReads).where(
                ForumThreadReads.user_id == 1, ForumThreadReads.thread_id == thread_id
            )
        )
        row = result.scalars().first()
        assert row.last_read_at.day == 2

"""Integration tests proving recompute_thread_stats is safe under concurrency.

Postgres runs READ COMMITTED (app.core.database sets no isolation override),
so every statement sees all rows committed before it starts. After a route
acquires the thread-row FOR UPDATE lock, recompute_thread_stats's plain
COUNT/MAX must therefore see a sibling post committed by an overlapping
"reply to thread" transaction, even though this transaction began (and ran
its first read, e.g. get_current_user's SELECT) before that commit.

These tests reproduce that ordering deterministically with two independent
sessions on two independent connections -- no threads/asyncio.gather needed,
since the point is read *timing*, not true parallel execution:

1. Session A creates the thread + opening post and commits.
2. Session B issues a plain read (its first read) *before* session A's reply
   commits.
3. Session A locks the thread (FOR UPDATE), adds a reply, recomputes, commits.
4. Session B locks the thread (now free), adds its own reply, recomputes,
   commits.
5. A fresh session re-reads the thread: post_count must reflect all three
   posts (opening + both replies), not just the two B's earlier read saw.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.services.forum import recompute_thread_stats


@pytest.mark.integration
@pytest.mark.needs_commit
class TestRecomputeThreadStatsConcurrency:
    async def test_concurrent_replies_do_not_lose_committed_sibling_post(
        self, db_session: AsyncSession, engine: AsyncEngine
    ) -> None:
        """B's recompute must count A's committed reply even though B's
        transaction began before A's commit.

        A recompute that missed it would finish with post_count == 2
        (opening post + B's own reply), silently dropping A's post.
        """
        # --- Session A (db_session): category, thread, opening post ---
        category = ForumCategories(title="Concurrency Category")
        db_session.add(category)
        await db_session.flush()

        thread = ForumThreads(
            category_id=category.category_id, title="Concurrency Thread", user_id=1
        )
        db_session.add(thread)
        await db_session.flush()

        opening_post = ForumPosts(thread_id=thread.thread_id, user_id=1, post_text="opening")
        db_session.add(opening_post)
        await db_session.flush()
        await db_session.refresh(opening_post)

        thread.post_count = 1
        thread.last_post_at = opening_post.date
        thread.last_post_user_id = 1
        await db_session.commit()

        thread_id = thread.thread_id
        assert thread_id is not None

        # --- Session B: independent connection/transaction ---
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        session_b = session_maker()
        try:
            # B's first read -- this precedes A's commit, mirroring how
            # get_current_user's SELECT runs in a real request long before the
            # route locks the thread row.
            # Deliberately plain (non-locking); not committed/rolled back.
            await session_b.execute(select(ForumThreads).where(ForumThreads.thread_id == thread_id))

            # --- Session A: lock thread, add reply (user 2), recompute, commit ---
            result_a = await db_session.execute(
                select(ForumThreads).where(ForumThreads.thread_id == thread_id).with_for_update()
            )
            thread_a = result_a.scalar_one()
            db_session.add(ForumPosts(thread_id=thread_id, user_id=2, post_text="reply from A"))
            await db_session.flush()
            await recompute_thread_stats(db_session, thread_a)
            await db_session.commit()

            # --- Session B: lock thread (free now), add its own reply (user 3), recompute, commit ---
            result_b = await session_b.execute(
                select(ForumThreads).where(ForumThreads.thread_id == thread_id).with_for_update()
            )
            thread_b = result_b.scalar_one()
            session_b.add(ForumPosts(thread_id=thread_id, user_id=3, post_text="reply from B"))
            await session_b.flush()
            await recompute_thread_stats(session_b, thread_b)
            await session_b.commit()
        finally:
            await session_b.close()

        # --- Verify from a third, fresh session: nothing was lost ---
        session_c = session_maker()
        try:
            result_c = await session_c.execute(
                select(ForumThreads).where(ForumThreads.thread_id == thread_id)
            )
            final_thread = result_c.scalar_one()
            assert final_thread.post_count == 3
            assert final_thread.last_post_user_id == 3
        finally:
            await session_c.close()

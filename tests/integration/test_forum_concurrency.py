"""Integration tests proving recompute_thread_stats is safe under concurrency.

InnoDB defaults to REPEATABLE READ (app.core.database sets no isolation
override). A plain (non-locking) read inside a transaction is evaluated
against that transaction's consistent snapshot, fixed at its *first* read --
in a real request, get_current_user's SELECT, long before a route acquires
the thread-row FOR UPDATE lock. If recompute_thread_stats's COUNT/MAX used a
plain read, two overlapping "reply to thread" transactions could silently
under-count posts: the second transaction's snapshot predates the first
transaction's commit, so its COUNT misses the sibling post even though it
correctly waits for (and acquires) the thread-row lock afterwards.

These tests reproduce that ordering deterministically with two independent
sessions on two independent connections -- no threads/asyncio.gather needed,
since the whole point is snapshot *timing*, not true parallel execution:

1. Session A creates the thread + opening post and commits.
2. Session B issues a plain read (its first read), fixing its REPEATABLE READ
   snapshot *before* session A's reply commits.
3. Session A locks the thread (FOR UPDATE), adds a reply, recomputes, commits.
4. Session B locks the thread (now free), adds its own reply, recomputes,
   commits.
5. A fresh session re-reads the thread: post_count must reflect all three
   posts (opening + both replies), not just the two session B's stale
   snapshot could see.

NOTE on innodb_snapshot_isolation (MariaDB-only, ON by default on the
mariadb:12 image this project's dev/CI stack uses -- see
docker-compose.yml): when ON, MariaDB refuses to let a transaction take a
*locking* read (FOR UPDATE) on a row that was committed-over since that
transaction's snapshot was established -- it raises ER_CHECKREAD (1020)
instead of silently returning the newer row. That guard is real and
independently verified (a raw two-connection pymysql script reproduces it
outside of SQLAlchemy/the app entirely), but it fires purely because step 4
re-locks the exact ForumThreads row step 3 already committed a change to --
before recompute_thread_stats's own (patched-or-not) queries are ever
reached. It reproduces identically whether the recompute_thread_stats fix is
applied or reverted, so it cannot be what's discriminating the bug from the
fix here. It also does not run on vanilla MySQL/InnoDB, and it isn't
guaranteed to run in every MariaDB deployment (older versions default it
OFF; it's a session-settable variable). Session B disables it for itself
below so this test exercises the portable REPEATABLE-READ behavior the
production bug report is about, rather than being a no-op that only
ever proves MariaDB's own guard works.
"""

import pytest
from sqlalchemy import select, text
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
        transaction snapshot predates A's commit.

        Without locking reads in recompute_thread_stats, B's plain COUNT/MAX
        would still see its pre-A snapshot (opening post + B's own reply
        only) and finish with post_count == 2, silently dropping A's post.
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
            # See the module docstring: disable MariaDB's
            # innodb_snapshot_isolation guard for this session only, so B's
            # later FOR UPDATE (step 4) reproduces portable REPEATABLE-READ
            # semantics (silently returns the newer row) instead of MariaDB
            # raising ER_CHECKREAD -- which would mask the bug this test
            # targets rather than exercise it.
            await session_b.execute(text("SET SESSION innodb_snapshot_isolation = OFF"))

            # B's first read -- this is what fixes its REPEATABLE READ
            # snapshot, mirroring how get_current_user's SELECT fixes a real
            # request's snapshot long before the route locks the thread row.
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

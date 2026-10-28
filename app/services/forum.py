"""
Forum helpers: category access checks, denormalized thread stats, read tracking.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumPosts, ForumThreadReads, ForumThreads


def can_access(user_perms: set[str], required_perm: str | None) -> bool:
    """True when a category action gated by required_perm is allowed.

    required_perm None means ungated (authentication requirements are
    enforced separately by the route).
    """
    return required_perm is None or required_perm in user_perms


async def recompute_thread_stats(db: AsyncSession, thread: ForumThreads) -> None:
    """Recompute post_count/last_post_at/last_post_user_id from live posts.

    Always recompute — never increment — so the counters cannot drift.
    Caller must hold the thread row lock (SELECT ... FOR UPDATE) when other
    writers may race, and is responsible for committing.

    Plain reads are enough: Postgres runs READ COMMITTED (app.core.database
    sets no isolation override), so each statement takes a fresh snapshot.
    Once the caller holds the thread-row lock, the COUNT/MAX below sees every
    sibling post committed before that lock was granted.
    """
    result = await db.execute(
        select(func.count(), func.max(ForumPosts.post_id))
        .where(ForumPosts.thread_id == thread.thread_id)  # type: ignore[arg-type]
        .where(ForumPosts.deleted == False)  # type: ignore[arg-type]  # noqa: E712
    )
    count, last_post_id = result.one()
    thread.post_count = count or 0
    if last_post_id is None:
        thread.last_post_at = None
        thread.last_post_user_id = None
    else:
        last_post = (
            await db.execute(select(ForumPosts).where(ForumPosts.post_id == last_post_id))
        ).scalar_one()
        thread.last_post_at = last_post.date
        thread.last_post_user_id = last_post.user_id


async def upsert_thread_read(
    db: AsyncSession, user_id: int, thread_id: int, read_at: datetime
) -> None:
    """Record that user has seen the thread as of read_at. Caller commits."""
    stmt = pg_insert(ForumThreadReads).values(
        user_id=user_id, thread_id=thread_id, last_read_at=read_at
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "thread_id"], set_={"last_read_at": stmt.excluded.last_read_at}
    )
    await db.execute(stmt)

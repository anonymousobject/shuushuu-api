"""
Forum helpers: category access checks, denormalized thread stats, read tracking.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
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
        last_post = await db.get(ForumPosts, last_post_id)
        assert last_post is not None
        thread.last_post_at = last_post.date
        thread.last_post_user_id = last_post.user_id


async def upsert_thread_read(
    db: AsyncSession, user_id: int, thread_id: int, read_at: datetime
) -> None:
    """Record that user has seen the thread as of read_at. Caller commits."""
    stmt = mysql_insert(ForumThreadReads).values(
        user_id=user_id, thread_id=thread_id, last_read_at=read_at
    )
    stmt = stmt.on_duplicate_key_update(last_read_at=stmt.inserted.last_read_at)
    await db.execute(stmt)

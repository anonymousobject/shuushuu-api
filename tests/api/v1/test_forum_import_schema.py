"""Provenance columns exist and the Archived User is created at import run time."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import ARCHIVED_USERNAME, ensure_archived_user, get_archived_user_id
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.models.user import Users


async def test_legacy_columns_round_trip(db_session: AsyncSession):
    cat = ForumCategories(title="Imp", legacy_forum_id=7)
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="t", user_id=1, legacy_topic_id=42)
    db_session.add(thread)
    await db_session.flush()
    post = ForumPosts(
        thread_id=thread.thread_id, user_id=1, post_text="hi",
        legacy_post_id=99, legacy_poster_id=1234, legacy_username="OldName",
    )
    db_session.add(post)
    await db_session.commit()
    fetched = await db_session.get(ForumPosts, post.post_id)
    assert fetched.legacy_post_id == 99
    assert fetched.legacy_poster_id == 1234
    assert fetched.legacy_username == "OldName"


async def test_ensure_archived_user_creates_and_is_idempotent(db_session: AsyncSession):
    first_id = await ensure_archived_user(db_session)
    user = await db_session.get(Users, first_id)
    assert user is not None
    assert user.username == ARCHIVED_USERNAME
    assert user.active == 0
    assert await get_archived_user_id(db_session) == first_id
    # A second call must not create a duplicate; it returns the same id.
    assert await ensure_archived_user(db_session) == first_id

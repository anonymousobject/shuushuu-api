"""Round-trip smoke tests: forum models insert/read against the migrated schema."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forum import ForumCategories, ForumPosts, ForumThreadReads, ForumThreads


async def test_forum_tables_round_trip(db_session: AsyncSession):
    """Insert one row per forum table through the ORM and read it back."""
    cat = ForumCategories(title="Smoke Category", description="desc", sort_order=1)
    db_session.add(cat)
    await db_session.flush()

    thread = ForumThreads(category_id=cat.category_id, title="Smoke thread", user_id=1)
    db_session.add(thread)
    await db_session.flush()

    post = ForumPosts(thread_id=thread.thread_id, user_id=1, post_text="hello")
    db_session.add(post)
    await db_session.flush()
    await db_session.refresh(post)

    read = ForumThreadReads(user_id=1, thread_id=thread.thread_id, last_read_at=post.date)
    db_session.add(read)
    await db_session.commit()

    fetched = await db_session.get(ForumThreads, thread.thread_id)
    assert fetched is not None
    assert fetched.pinned is False
    assert fetched.locked is False
    assert fetched.deleted is False
    assert fetched.post_count == 0  # denormalized fields start at defaults
    assert post.date is not None  # server_default filled


async def test_forum_permissions_in_enum():
    """The four forum permissions exist and the whitelist holds the two access tiers."""
    from app.core.permissions import FORUM_ACCESS_PERMISSIONS, Permission

    assert Permission.FORUM_ACCESS_STAFF.value == "forum_access_staff"
    assert Permission.FORUM_ACCESS_TAGGER.value == "forum_access_tagger"
    assert Permission.FORUM_MODERATE.value == "forum_moderate"
    assert Permission.FORUM_CATEGORY_MANAGE.value == "forum_category_manage"
    assert FORUM_ACCESS_PERMISSIONS == {"forum_access_staff", "forum_access_tagger"}

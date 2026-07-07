"""Imported posts attributed to the Archived User display the original name."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import ensure_archived_user
from app.models.forum import ForumCategories, ForumPosts, ForumThreads


async def test_archived_post_shows_legacy_username(client: AsyncClient, db_session: AsyncSession):
    # The import creates the Archived User at run time; create it here for the test.
    archived_id = await ensure_archived_user(db_session)

    cat = ForumCategories(title="Imported")
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="Old thread", user_id=archived_id,
                          locked=True, legacy_topic_id=1)
    db_session.add(thread)
    await db_session.flush()
    db_session.add(ForumPosts(
        thread_id=thread.thread_id, user_id=archived_id, post_text="old body",
        legacy_post_id=1, legacy_poster_id=555, legacy_username="RetroPoster",
    ))
    await db_session.commit()

    resp = await client.get(f"/api/v1/forum/threads/{thread.thread_id}")
    assert resp.status_code == 200
    post = resp.json()["posts"][0]
    assert post["user"]["username"] == "RetroPoster"  # not "Archived User"
    assert post["user_id"] == archived_id

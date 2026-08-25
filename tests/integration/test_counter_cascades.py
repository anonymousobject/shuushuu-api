"""Counters must survive FK-cascaded deletes on Postgres.

The selling point of the trigger port (app/core/pg_triggers.py, PR #353):
deleting an image cascades away its tag_links/favorites/posts rows, and on
Postgres the counter triggers fire for those cascaded deletes. InnoDB does
not fire triggers on cascades, so on MariaDB these counters silently drift on
every image deletion — which is why this test is postgres_only: it asserts
the *correct* behavior, which only Postgres exhibits.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comments, Images, Tags, Users
from app.models.favorite import Favorites
from app.models.tag_link import TagLinks

pytestmark = [pytest.mark.integration, pytest.mark.postgres_only]


async def _counters(db: AsyncSession, user_id: int, tag_id: int) -> dict[str, int]:
    user = (
        await db.execute(
            select(Users.posts, Users.image_posts, Users.favorites).where(  # type: ignore[call-overload]
                Users.user_id == user_id
            )
        )
    ).one()
    usage_count = (
        await db.execute(select(Tags.usage_count).where(Tags.tag_id == tag_id))  # type: ignore[call-overload]
    ).scalar_one()
    return {
        "user_posts": user.posts,
        "user_image_posts": user.image_posts,
        "user_favorites": user.favorites,
        "tag_usage_count": usage_count,
    }


async def test_image_delete_cascade_maintains_counters(
    db_session: AsyncSession, test_user, test_image, test_tag
):
    """Deleting an image must decrement every counter its cascaded rows fed."""
    # Plain ints up front: expire_all() below invalidates the ORM objects, and
    # touching an expired attribute lazy-loads synchronously (MissingGreenlet).
    user_id = test_user.user_id
    image_id = test_image.image_id
    tag_id = test_tag.tag_id

    # Attach a tag, a favorite, and a comment to the image; the insert
    # triggers bump the counters.
    db_session.add(TagLinks(image_id=image_id, tag_id=tag_id, user_id=user_id))
    db_session.add(Favorites(image_id=image_id, user_id=user_id, fav_date=datetime.now(UTC)))
    db_session.add(
        Comments(
            image_id=image_id,
            user_id=user_id,
            post_text="cascade probe",
            date=datetime.now(UTC),
            update_count=0,
        )
    )
    await db_session.commit()

    db_session.expire_all()  # trigger writes bypass the identity map
    before = await _counters(db_session, user_id, tag_id)
    assert before["tag_usage_count"] >= 1
    assert before["user_favorites"] >= 1
    assert before["user_posts"] >= 1
    assert before["user_image_posts"] >= 1

    # The same statement the image-delete endpoint issues: the tag_link,
    # favorite, and comment rows go away via FK CASCADE, not via app code.
    await db_session.execute(delete(Images).where(Images.image_id == image_id))  # type: ignore[arg-type]
    await db_session.commit()

    db_session.expire_all()
    after = await _counters(db_session, user_id, tag_id)
    assert after["tag_usage_count"] == before["tag_usage_count"] - 1
    assert after["user_favorites"] == before["user_favorites"] - 1
    assert after["user_posts"] == before["user_posts"] - 1
    # images_counters_delete fires on the image row itself (not a cascade).
    assert after["user_image_posts"] == before["user_image_posts"] - 1

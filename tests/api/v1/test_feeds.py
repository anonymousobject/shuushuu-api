"""Integration tests for Atom feed endpoints and query helpers."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models import Images, Tags, TagLinks, Users
from app.services.feeds import fetch_feed_entries, fetch_feed_sentinel


async def _make_user(db: AsyncSession, username: str = "feeder") -> Users:
    user = Users(
        username=username,
        password="x",
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_image(
    db: AsyncSession,
    user: Users,
    filename: str,
    status: int = ImageStatus.ACTIVE,
) -> Images:
    image = Images(
        filename=filename,
        ext="png",
        status=status,
        user_id=user.user_id,
        filesize=1024,
        date_added=datetime.now(UTC),
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


async def _make_tag(
    db: AsyncSession, title: str, type_: int = TagType.THEME
) -> Tags:
    tag = Tags(title=title, type=type_, user_id=None)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def _link(db: AsyncSession, image: Images, tag: Tags) -> None:
    db.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id))
    await db.commit()


class TestFetchFeedSentinelGlobal:
    async def test_returns_only_active_images_newest_first(
        self, db_session: AsyncSession
    ):
        user = await _make_user(db_session)
        active_a = await _make_image(db_session, user, "a")
        hidden = await _make_image(db_session, user, "h", status=ImageStatus.INAPPROPRIATE)
        active_b = await _make_image(db_session, user, "b")
        await _make_image(db_session, user, "c", status=ImageStatus.REVIEW)

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=None, limit=50)

        ids = [row[0] for row in sentinel]
        assert active_b.image_id in ids
        assert active_a.image_id in ids
        assert hidden.image_id not in ids
        assert ids.index(active_b.image_id) < ids.index(active_a.image_id)

    async def test_respects_limit(self, db_session: AsyncSession):
        user = await _make_user(db_session, "limituser")
        for i in range(10):
            await _make_image(db_session, user, f"lim{i}")

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=None, limit=3)
        assert len(sentinel) == 3


class TestFetchFeedSentinelPerTag:
    async def test_filters_by_tag_id(self, db_session: AsyncSession):
        user = await _make_user(db_session, "tagfilter")
        tag = await _make_tag(db_session, "filtertag")
        img_with = await _make_image(db_session, user, "with")
        img_without = await _make_image(db_session, user, "without")
        await _link(db_session, img_with, tag)

        sentinel = await fetch_feed_sentinel(
            db_session, tag_ids=[tag.tag_id], limit=50
        )

        ids = [row[0] for row in sentinel]
        assert img_with.image_id in ids
        assert img_without.image_id not in ids

    async def test_multiple_tag_ids_union(self, db_session: AsyncSession):
        """tag_ids represents the already-expanded hierarchy set; any match qualifies."""
        user = await _make_user(db_session, "multitag")
        t1 = await _make_tag(db_session, "t1")
        t2 = await _make_tag(db_session, "t2")
        img_a = await _make_image(db_session, user, "a_t1")
        img_b = await _make_image(db_session, user, "b_t2")
        await _link(db_session, img_a, t1)
        await _link(db_session, img_b, t2)

        sentinel = await fetch_feed_sentinel(
            db_session, tag_ids=[t1.tag_id, t2.tag_id], limit=50
        )

        ids = [row[0] for row in sentinel]
        assert img_a.image_id in ids
        assert img_b.image_id in ids

    async def test_empty_tag_list_returns_no_rows(self, db_session: AsyncSession):
        sentinel = await fetch_feed_sentinel(db_session, tag_ids=[], limit=50)
        assert sentinel == []


class TestFetchFeedEntriesGlobal:
    async def test_returns_image_detailed_responses(self, db_session: AsyncSession):
        user = await _make_user(db_session, "hydrator")
        tag = await _make_tag(db_session, "hydrator_tag", type_=TagType.ARTIST)
        image = await _make_image(db_session, user, "h1")
        await _link(db_session, image, tag)

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)

        assert len(entries) >= 1
        entry = next(e for e in entries if e.image_id == image.image_id)
        assert entry.user is not None
        assert entry.user.username == "hydrator"
        assert entry.tags is not None
        assert any(t.tag == "hydrator_tag" for t in entry.tags)

    async def test_only_active_images(self, db_session: AsyncSession):
        user = await _make_user(db_session, "activeonly")
        active = await _make_image(db_session, user, "a")
        hidden = await _make_image(db_session, user, "h", status=ImageStatus.INAPPROPRIATE)

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)
        ids = [e.image_id for e in entries]
        assert active.image_id in ids
        assert hidden.image_id not in ids

    async def test_ordered_newest_first(self, db_session: AsyncSession):
        user = await _make_user(db_session, "orderer")
        first = await _make_image(db_session, user, "o1")
        second = await _make_image(db_session, user, "o2")

        entries = await fetch_feed_entries(db_session, tag_ids=None, limit=50)
        ids = [e.image_id for e in entries]
        assert ids.index(second.image_id) < ids.index(first.image_id)

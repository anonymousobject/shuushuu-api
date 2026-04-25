"""Integration tests for Atom feed endpoints and query helpers."""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models import Images, TagLinks, Tags, Users
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


async def _make_tag(db: AsyncSession, title: str, type_: int = TagType.THEME) -> Tags:
    tag = Tags(title=title, type=type_, user_id=None)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def _link(db: AsyncSession, image: Images, tag: Tags) -> None:
    db.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id))
    await db.commit()


class TestFetchFeedSentinelGlobal:
    async def test_returns_only_active_images_newest_first(self, db_session: AsyncSession):
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

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=[tag.tag_id], limit=50)

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

        sentinel = await fetch_feed_sentinel(db_session, tag_ids=[t1.tag_id, t2.tag_id], limit=50)

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


class TestGlobalImagesFeed:
    async def test_returns_atom_content_type(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "ctuser")
        await _make_image(db_session, user, "ct1")
        response = await client.get("/api/v1/images.atom")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/atom+xml")

    async def test_includes_only_active_images(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "actuser")
        active = await _make_image(db_session, user, "a1")
        hidden = await _make_image(db_session, user, "h1", status=ImageStatus.INAPPROPRIATE)
        response = await client.get("/api/v1/images.atom")
        body = response.text
        assert f"image:{active.image_id}" in body
        assert f"image:{hidden.image_id}" not in body

    async def test_caps_at_50_entries(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "capuser")
        for i in range(55):
            await _make_image(db_session, user, f"cap{i}")
        response = await client.get("/api/v1/images.atom")
        assert response.status_code == 200
        assert response.text.count("<entry") <= 50

    async def test_sets_cache_control_header(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "cacheuser")
        await _make_image(db_session, user, "c1")
        response = await client.get("/api/v1/images.atom")
        assert "max-age=300" in response.headers["cache-control"]

    async def test_sets_etag_and_last_modified(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "etaguser")
        await _make_image(db_session, user, "e1")
        response = await client.get("/api/v1/images.atom")
        assert response.headers.get("etag", "").startswith('W/"')
        assert "last-modified" in response.headers

    async def test_conditional_if_none_match_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "condetag")
        await _make_image(db_session, user, "ce1")
        first = await client.get("/api/v1/images.atom")
        etag = first.headers["etag"]
        second = await client.get("/api/v1/images.atom", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.text == ""

    async def test_if_none_match_accepts_comma_separated_list(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """RFC 7232 §3.2: clients may send multiple ETags. We 304 if ours is in the set."""
        user = await _make_user(db_session, "multietag")
        await _make_image(db_session, user, "me1")
        first = await client.get("/api/v1/images.atom")
        etag = first.headers["etag"]
        second = await client.get(
            "/api/v1/images.atom",
            headers={"If-None-Match": f'W/"stale-old-etag", {etag}, W/"another-old"'},
        )
        assert second.status_code == 304

    async def test_if_none_match_star_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "staretag")
        await _make_image(db_session, user, "se1")
        response = await client.get("/api/v1/images.atom", headers={"If-None-Match": "*"})
        assert response.status_code == 304

    async def test_new_image_busts_etag(self, client: AsyncClient, db_session: AsyncSession):
        user = await _make_user(db_session, "bustetag")
        await _make_image(db_session, user, "be1")
        first = await client.get("/api/v1/images.atom")
        first_etag = first.headers["etag"]
        await _make_image(db_session, user, "be2")
        second = await client.get("/api/v1/images.atom", headers={"If-None-Match": first_etag})
        assert second.status_code == 200
        assert second.headers["etag"] != first_etag

    async def test_if_modified_since_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "imsuser")
        await _make_image(db_session, user, "ims1")
        first = await client.get("/api/v1/images.atom")
        last_mod = first.headers["last-modified"]
        second = await client.get("/api/v1/images.atom", headers={"If-Modified-Since": last_mod})
        assert second.status_code == 304

    async def test_empty_feed_is_200(self, client: AsyncClient, db_session: AsyncSession):
        response = await client.get("/api/v1/images.atom")
        assert response.status_code == 200
        assert "<feed" in response.text


class TestPerTagImagesFeed:
    async def test_returns_only_images_with_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "ptuser")
        tag = await _make_tag(db_session, "pt_tag")
        with_tag = await _make_image(db_session, user, "pt_w")
        without_tag = await _make_image(db_session, user, "pt_wo")
        await _link(db_session, with_tag, tag)
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/images.atom")
        assert response.status_code == 200
        body = response.text
        assert f"image:{with_tag.image_id}" in body
        assert f"image:{without_tag.image_id}" not in body

    async def test_unknown_tag_id_returns_404(self, client: AsyncClient):
        response = await client.get("/api/v1/tags/999999999/images.atom")
        assert response.status_code == 404

    async def test_alias_tag_serves_canonical_image_set(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "aliasuser")
        canonical = await _make_tag(db_session, "canonical_tag")
        alias = Tags(
            title="alias_tag",
            type=TagType.THEME,
            user_id=None,
            alias_of=canonical.tag_id,
        )
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        image = await _make_image(db_session, user, "aliasimg")
        await _link(db_session, image, canonical)

        response = await client.get(f"/api/v1/tags/{alias.tag_id}/images.atom")
        assert response.status_code == 200
        assert f"image:{image.image_id}" in response.text

    async def test_conditional_request_returns_304(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await _make_user(db_session, "pt_etag_user")
        tag = await _make_tag(db_session, "pt_etag_tag")
        image = await _make_image(db_session, user, "pt_e1")
        await _link(db_session, image, tag)

        first = await client.get(f"/api/v1/tags/{tag.tag_id}/images.atom")
        assert first.status_code == 200
        etag = first.headers["etag"]

        second = await client.get(
            f"/api/v1/tags/{tag.tag_id}/images.atom",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304

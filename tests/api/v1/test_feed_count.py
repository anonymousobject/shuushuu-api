"""Equivalence tests for the fast default-feed pagination count.

list_images replaces the naive `count(visible OR mine)` (a full-table scan) with a
hidden-complement count for the *bare* default feed. These tests assert the endpoint's
`total` equals an independent ground-truth count for every viewer type AND that any
explicit filter falls back to the exact count (i.e. the fast path is never wrongly
applied). Correctness here is data-independent, so it holds on the small test DB.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.user import Users
from app.services.image_visibility import PUBLIC_IMAGE_STATUSES

# Import the source of truth rather than re-listing the statuses, so this test
# can't silently diverge if the public-status set ever changes.
VISIBLE = sorted(PUBLIC_IMAGE_STATUSES)


async def _user(db, username, show_all=0, hide_reposts=0):
    u = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
        show_all_images=show_all,
        hide_reposts=hide_reposts,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _img(db, owner, md5, status):
    img = Images(
        filename="fc",
        ext="jpg",
        md5_hash=md5,
        user_id=owner.user_id,
        width=10,
        height=10,
        filesize=100,
        status=status,
    )
    db.add(img)
    await db.commit()
    return img


async def _login(client, username):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": "TestPassword123!"})
    assert r.status_code == 200
    return r.json()["access_token"]


async def _ground_truth(db: AsyncSession, where=None) -> int:
    q = select(func.count()).select_from(Images)
    if where is not None:
        q = q.where(where)
    return (await db.execute(q)).scalar() or 0


async def _seed(db, a, b):
    # a: 2 visible (active, spoiler) + 2 hidden (deactivated, review)
    await _img(db, a, "a" * 32, 1)
    await _img(db, a, "a1" + "0" * 30, 2)
    await _img(db, a, "a2" + "0" * 30, 0)
    await _img(db, a, "a3" + "0" * 30, -4)
    # b: 3 visible (active, active, repost) + 1 hidden (deactivated)
    await _img(db, b, "b" * 32, 1)
    await _img(db, b, "b1" + "0" * 30, 1)
    await _img(db, b, "b2" + "0" * 30, -1)
    await _img(db, b, "b3" + "0" * 30, 0)


@pytest.mark.api
class TestFastFeedCount:
    async def test_bare_feed_total_matches_ground_truth_all_viewers(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        a = await _user(db_session, "fcA", show_all=0)
        b = await _user(db_session, "fcB", show_all=0)
        await _seed(db_session, a, b)

        # Anonymous: only public statuses.
        expected_anon = await _ground_truth(db_session, Images.status.in_(VISIBLE))
        r = await client.get("/api/v1/images/?per_page=1")
        assert r.json()["total"] == expected_anon

        # Logged-in, show_all=0: public OR own (incl. own hidden).
        expected_a = await _ground_truth(
            db_session, or_(Images.status.in_(VISIBLE), Images.user_id == a.user_id)
        )
        token = await _login(client, a.username)
        r = await client.get("/api/v1/images/?per_page=1", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["total"] == expected_a
        # sanity: a has hidden images, so this strictly exceeds the anon total
        assert expected_a > expected_anon

    async def test_bare_feed_show_all_counts_everything(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        a = await _user(db_session, "fcShowAll", show_all=1)
        b = await _user(db_session, "fcShowAllB", show_all=0)
        await _seed(db_session, a, b)

        expected_all = await _ground_truth(db_session)
        token = await _login(client, a.username)
        r = await client.get("/api/v1/images/?per_page=1", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["total"] == expected_all

    async def test_explicit_filters_fall_back_to_exact_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # show_all=1 viewer so a content filter composes cleanly with no visibility OR.
        viewer = await _user(db_session, "fcFilterV", show_all=1)
        b = await _user(db_session, "fcFilterB", show_all=0)
        await _seed(db_session, viewer, b)
        token = await _login(client, viewer.username)
        h = {"Authorization": f"Bearer {token}"}

        # Explicit status overrides visibility entirely -> exact count of that status,
        # NOT the whole-feed fast count. (Guards against the fast path wrongly applying.)
        expected_deact = await _ground_truth(db_session, Images.status == 0)
        assert expected_deact > 0
        r = await client.get("/api/v1/images/?status=0&per_page=1", headers=h)
        assert r.json()["total"] == expected_deact

        # Uploader filter under show_all=1 -> exact count for that user (no visibility OR).
        expected_b = await _ground_truth(db_session, Images.user_id == b.user_id)
        r = await client.get(f"/api/v1/images/?user_id={b.user_id}&per_page=1", headers=h)
        assert r.json()["total"] == expected_b


    async def test_bare_feed_total_excludes_reposts_for_hide_reposts_viewer(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        a = await _user(db_session, "fcHRa", show_all=0)
        b = await _user(db_session, "fcHRb", show_all=0)
        await _seed(db_session, a, b)  # b owns exactly 1 repost (status -1)
        viewer = await _user(db_session, "fcHRview", show_all=0, hide_reposts=1)

        expected = await _ground_truth(
            db_session,
            and_(
                or_(Images.status.in_(VISIBLE), Images.user_id == viewer.user_id),
                Images.status != ImageStatus.REPOST,
            ),
        )
        token = await _login(client, viewer.username)
        r = await client.get("/api/v1/images/?per_page=1", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["total"] == expected

        # Sanity: hiding reposts strictly reduces the total (the seed has a repost).
        expected_no_hide = await _ground_truth(
            db_session, or_(Images.status.in_(VISIBLE), Images.user_id == viewer.user_id)
        )
        assert expected < expected_no_hide

    async def test_own_repost_not_double_counted_for_hide_reposts(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await _user(db_session, "fcOwnRep", show_all=0, hide_reposts=1)
        await _img(db_session, owner, "or0" + "0" * 29, 1)   # own active (visible)
        await _img(db_session, owner, "or1" + "1" * 29, -1)  # own repost (public, hidden by pref)
        await _img(db_session, owner, "or2" + "2" * 29, 0)   # own deactivated (hidden, but own -> visible)

        expected = await _ground_truth(
            db_session,
            and_(
                or_(Images.status.in_(VISIBLE), Images.user_id == owner.user_id),
                Images.status != ImageStatus.REPOST,
            ),
        )
        token = await _login(client, owner.username)
        r = await client.get("/api/v1/images/?per_page=1", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["total"] == expected


@pytest.mark.api
class TestFeedCountCache:
    async def test_global_counts_cached_within_ttl(self, db_session: AsyncSession, redis_client):
        """The three global counts are TTL-cached (no per-mutation invalidation): a new
        image isn't reflected until the entry expires or is cleared. Also guards the
        str<->int round-trip through Redis."""
        from app.services.feed_count_cache import _KEY_HIDDEN, _KEY_REPOST, _KEY_TOTAL, get_feed_counts

        await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN, _KEY_REPOST)
        try:
            a = await _user(db_session, "fcCache", show_all=0)
            await _img(db_session, a, "c" * 32, 1)
            await _img(db_session, a, "c1" + "0" * 30, 0)

            total1, hidden1, repost1 = await get_feed_counts(db_session, redis_client)
            assert isinstance(total1, int) and isinstance(hidden1, int) and isinstance(repost1, int)  # parsed back from str

            # New image is NOT reflected while the cache is warm (TTL, no invalidation).
            await _img(db_session, a, "c2" + "0" * 30, 1)
            assert await get_feed_counts(db_session, redis_client) == (total1, hidden1, repost1)

            # Once the entry is gone, a recompute picks the new image up.
            await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN, _KEY_REPOST)
            total3, _h, _r = await get_feed_counts(db_session, redis_client)
            assert total3 == total1 + 1
        finally:
            await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN, _KEY_REPOST)

    async def test_feed_counts_include_repost_count(self, db_session: AsyncSession, redis_client):
        from app.services.feed_count_cache import _KEY_HIDDEN, _KEY_REPOST, _KEY_TOTAL, get_feed_counts

        await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN, _KEY_REPOST)
        try:
            u = await _user(db_session, "fcRepost", show_all=0)
            await _img(db_session, u, "fr0" + "0" * 29, 1)   # active
            await _img(db_session, u, "fr1" + "1" * 29, -1)  # repost
            total, hidden, repost = await get_feed_counts(db_session, redis_client)
            assert isinstance(repost, int) and repost >= 1
        finally:
            await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN, _KEY_REPOST)


@pytest.mark.unit
class TestFeedFilters:
    """Unit guards for _FeedFilters, the bare-default-feed detector behind the fast count."""

    def test_bare_sentinel_has_every_field_empty(self):
        """`_FeedFilters()` is the bare sentinel only if every field defaults to None — a
        non-None default would make the fast-count check treat a real filter as "empty"."""
        from dataclasses import fields

        from app.api.v1.images import _FeedFilters

        bare = _FeedFilters()
        assert all(getattr(bare, f.name) is None for f in fields(_FeedFilters))

    def test_any_single_filter_is_not_bare(self):
        from app.api.v1.images import _FeedFilters

        non_bare = [
            {"image_status": [1]},
            {"user_id": 5},
            {"favorited_by_user_id": 5},
            {"tags": "1"},
            {"exclude_tags": "1"},
            {"missing_tag_types": "1"},
            {"date_from": "2026-01-01"},
            {"date_to": "2026-01-01"},
            {"min_width": 1},
            {"max_width": 1},
            {"min_height": 1},
            {"max_height": 1},
            {"min_rating": 1.0},
            {"min_favorites": 1},
            {"min_num_ratings": 1},
            {"commenter": 5},
            {"commentsearch": "x"},
            {"hascomments": False},
            {"exclude_user_id": "5"},
        ]
        for kwargs in non_bare:
            assert _FeedFilters(**kwargs) != _FeedFilters(), kwargs

    def test_field_set_is_the_documented_filter_set(self):
        """Guards against drift: a new list_images content filter must be added here to be
        covered by the `== _FeedFilters()` fast-count check."""
        from dataclasses import fields

        from app.api.v1.images import _FeedFilters

        assert {f.name for f in fields(_FeedFilters)} == {
            "image_status",
            "user_id",
            "favorited_by_user_id",
            "tags",
            "exclude_tags",
            "missing_tag_types",
            "date_from",
            "date_to",
            "min_width",
            "max_width",
            "min_height",
            "max_height",
            "min_rating",
            "min_favorites",
            "min_num_ratings",
            "commenter",
            "commentsearch",
            "hascomments",
            "reported",
            "exclude_user_id",
        }

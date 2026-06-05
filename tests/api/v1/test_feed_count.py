"""Equivalence tests for the fast default-feed pagination count.

list_images replaces the naive `count(visible OR mine)` (a full-table scan) with a
hidden-complement count for the *bare* default feed. These tests assert the endpoint's
`total` equals an independent ground-truth count for every viewer type AND that any
explicit filter falls back to the exact count (i.e. the fast path is never wrongly
applied). Correctness here is data-independent, so it holds on the small test DB.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.image import Images
from app.models.user import Users

VISIBLE = [-1, 1, 2]  # REPOST, ACTIVE, SPOILER (PUBLIC_IMAGE_STATUSES)


async def _user(db, username, show_all=0):
    u = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="",
        email=f"{username}@example.com",
        active=1,
        show_all_images=show_all,
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


@pytest.mark.api
class TestFeedCountCache:
    async def test_global_counts_cached_within_ttl(self, db_session: AsyncSession, redis_client):
        """The two global counts are TTL-cached (no per-mutation invalidation): a new
        image isn't reflected until the entry expires or is cleared. Also guards the
        str<->int round-trip through Redis."""
        from app.services.feed_count_cache import _KEY_HIDDEN, _KEY_TOTAL, get_feed_counts

        await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN)
        try:
            a = await _user(db_session, "fcCache", show_all=0)
            await _img(db_session, a, "c" * 32, 1)
            await _img(db_session, a, "c1" + "0" * 30, 0)

            total1, hidden1 = await get_feed_counts(db_session, redis_client)
            assert isinstance(total1, int) and isinstance(hidden1, int)  # parsed back from str

            # New image is NOT reflected while the cache is warm (TTL, no invalidation).
            await _img(db_session, a, "c2" + "0" * 30, 1)
            assert await get_feed_counts(db_session, redis_client) == (total1, hidden1)

            # Once the entry is gone, a recompute picks the new image up.
            await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN)
            total3, _h = await get_feed_counts(db_session, redis_client)
            assert total3 == total1 + 1
        finally:
            await redis_client.delete(_KEY_TOTAL, _KEY_HIDDEN)

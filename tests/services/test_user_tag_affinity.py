import math

import pytest
from sqlalchemy import text

from app.config import ImageStatus, TagType
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.models.user_tag_affinity import UserTagAffinity
from app.services.user_tag_affinity import _LOCK_PREFIX, refresh_user_tag_affinity

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_table_roundtrip(db_session):
    db_session.add(
        UserTagAffinity(
            user_id=1,
            tag_id=2,
            pool_cnt=10,
            fav_count=8,
            upload_count=3,
            rated_count=6,
            rating_avg=8.5,
            lift=4.2,
            rating_delta=1.5,
            affinity=2.19,
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT pool_cnt, affinity FROM user_tag_affinity WHERE user_id=1 AND tag_id=2")
        )
    ).one()
    assert row.pool_cnt == 10
    assert row.affinity == pytest.approx(2.19)


async def test_updated_at_server_default(db_session):
    # The refresh job inserts via raw INSERT…SELECT that OMITS updated_at,
    # relying on the server default. ORM inserts send explicit NULL (SQLModel
    # materializes the None default), so test the raw path directly.
    await db_session.execute(
        text(
            "INSERT INTO user_tag_affinity "
            "(user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, affinity) "
            "VALUES (7, 8, 5, 5, 0, 0, 1.0)"
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT updated_at FROM user_tag_affinity WHERE user_id=7 AND tag_id=8")
        )
    ).one()
    assert row.updated_at is not None  # server_default filled it


def _user(db, user_id):
    # favorites/image_ratings/images all FK user_id -> users.user_id; the
    # conftest db_session fixture only seeds users 1-3, so synthetic ids used
    # by these tests (500, 600) need a real parent row.
    db.add(
        Users(
            user_id=user_id,
            username=f"taste{user_id}",
            password="testpassword",
            password_type="bcrypt",
            salt=f"testsalt{user_id:07d}",
            email=f"taste{user_id}@example.com",
        )
    )


def _img(db, image_id, user_id=1, status=ImageStatus.ACTIVE):
    # ext is NOT NULL with no default in the schema, so it must be supplied.
    db.add(Images(image_id=image_id, user_id=user_id, ext="jpg", status=status))


def _tag(db, tag_id, ttype, title, alias_of=None):
    db.add(Tags(tag_id=tag_id, type=ttype, title=title, alias_of=alias_of))


def _link(db, tag_id, image_id):
    db.add(TagLinks(tag_id=tag_id, image_id=image_id, user_id=1))


def _fav(db, user_id, image_id):
    db.add(Favorites(user_id=user_id, image_id=image_id))


def _rate(db, user_id, image_id, rating):
    db.add(ImageRatings(user_id=user_id, image_id=image_id, rating=rating))


async def _rows(db):
    from sqlalchemy import text

    res = await db.execute(
        text(
            "SELECT user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, "
            "rating_avg, lift, rating_delta, affinity FROM user_tag_affinity"
        )
    )
    return {(r.user_id, r.tag_id): r for r in res.all()}


REFRESH_KW = {"min_support": 2, "smoothing_k": 0, "beta": 0.5, "min_events": 3, "batch_size": 500}


async def test_lift_and_affinity_from_favorites(db_session):
    # World: 10 visible images. Tag 20 on images 1..2 only; tag 30 on all 10.
    # User 500 favorites images 1..2 (pool_size=2, both tags 20 and 30... tag 30 also on 1..2).
    # lift(500,20) = (2/2) / (2/10) = 5.0 ; lift(500,30) = (2/2) / (10/10) = 1.0
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "Niche")
    _tag(db_session, 30, TagType.THEME, "Generic")
    for i in range(1, 11):
        _img(db_session, i)
        _link(db_session, 30, i)
    for i in (1, 2):
        _link(db_session, 20, i)
    # Favorites/ImageRatings are composite-PK, relationship-less models;
    # flush their FK targets first so SQLAlchemy's flush ordering (which
    # doesn't infer a dependency here without a declared relationship())
    # doesn't attempt them before the images they reference exist.
    await db_session.flush()
    _fav(db_session, 500, 1)
    _fav(db_session, 500, 2)
    _rate(db_session, 500, 3, 7)  # 3rd event to clear min_events=3
    await db_session.commit()

    n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    assert n > 0
    rows = await _rows(db_session)
    r20 = rows[(500, 20)]
    r30 = rows[(500, 30)]
    assert r20.pool_cnt == 2 and r20.fav_count == 2 and r20.upload_count == 0
    assert r20.lift == pytest.approx(5.0)
    assert r30.lift == pytest.approx(1.0)
    # rel=1e-5: affinity is a single-precision FLOAT column (Task 1 schema),
    # which round-trips ln(5.0) as 1.60944 vs the double 1.6094379124341003
    # (~1.3e-6 relative error) -- past pytest's default 1e-6 rel tolerance.
    assert r20.affinity == pytest.approx(math.log(5.0), rel=1e-5)
    assert r30.affinity == pytest.approx(0.0)  # ln(1.0)


async def test_pool_dedupes_fav_of_own_upload(db_session):
    # User 500 uploads images 1-2 AND favorites both: each image counts ONCE
    # in the pool (pool_cnt=2, not 4), while fav_count and upload_count each
    # record their own axis.
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2):
        _img(db_session, i, user_id=500)
        _link(db_session, 20, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    _fav(db_session, 500, 1)
    _fav(db_session, 500, 2)  # 4 events total >= 3
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    r = rows[(500, 20)]
    assert r.pool_cnt == 2
    assert r.fav_count == 2
    assert r.upload_count == 2


async def test_rating_delta_is_centered_on_user_mean(db_session):
    # User 500 rates: images with tag 20 at 9,9 ; images with tag 30 at 5,5.
    # user_mean = 7.0 -> delta(20)=+2, delta(30)=-2.
    # affinity = beta * delta (no pool) -> +1.0 / -1.0 with beta=0.5.
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "Loved")
    _tag(db_session, 30, TagType.THEME, "Disliked")
    ratings = [(20, 9), (20, 9), (30, 5), (30, 5)]
    for i, (tag, _rating) in enumerate(ratings, start=1):
        _img(db_session, i)
        _link(db_session, tag, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i, (_tag_id, rating) in enumerate(ratings, start=1):
        _rate(db_session, 500, i, rating)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].rating_delta == pytest.approx(2.0)
    assert rows[(500, 30)].rating_delta == pytest.approx(-2.0)
    assert rows[(500, 20)].affinity == pytest.approx(1.0)
    assert rows[(500, 30)].affinity == pytest.approx(-1.0)  # negative rows are KEPT


async def test_min_support_gates_rows(db_session):
    # Tag 20 has pool support 2 (kept, min_support=2); tag 30 appears once (dropped).
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "Kept")
    _tag(db_session, 30, TagType.THEME, "Dropped")
    for i in (1, 2):
        _img(db_session, i)
        _link(db_session, 20, i)
    _img(db_session, 3)
    _link(db_session, 30, 3)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i in (1, 2, 3):
        _fav(db_session, 500, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert (500, 20) in rows
    assert (500, 30) not in rows


async def test_min_events_excludes_light_users(db_session):
    # User 500 has 3 events (profiled); user 600 has only 2 (not profiled).
    _user(db_session, 500)
    _user(db_session, 600)
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i in (1, 2, 3):
        _fav(db_session, 500, i)
    for i in (1, 2):
        _fav(db_session, 600, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert (500, 20) in rows
    assert all(uid != 600 for uid, _ in rows)


async def test_alias_links_resolve_to_canonical(db_session):
    # Tag 21 is an alias of 20; links via 21 count toward canonical 20.
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "Canonical")
    _tag(db_session, 21, TagType.SOURCE, "Alias", alias_of=20)
    for i in (1, 2):
        _img(db_session, i)
    _link(db_session, 20, 1)
    _link(db_session, 21, 2)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i in (1, 2):
        _fav(db_session, 500, i)
    _rate(db_session, 500, 1, 8)  # 3rd event
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 2
    assert (500, 21) not in rows


async def test_invisible_images_excluded(db_session):
    # DEACTIVATED image favorites don't count toward the pool.
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2):
        _img(db_session, i)
        _link(db_session, 20, i)
    for i in (3, 4):
        _img(db_session, i, status=ImageStatus.DEACTIVATED)
        _link(db_session, 20, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i in (1, 2, 3, 4):
        _fav(db_session, 500, i)
    await db_session.commit()

    await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 2


async def test_batching_covers_users_across_batches(db_session):
    # batch_size=1 forces one INSERT per user; both users must land.
    _user(db_session, 500)
    _user(db_session, 600)
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for uid in (500, 600):
        for i in (1, 2, 3):
            _fav(db_session, uid, i)
    await db_session.commit()

    kw = dict(REFRESH_KW)
    kw["batch_size"] = 1
    await refresh_user_tag_affinity(db_session, **kw)
    rows = await _rows(db_session)
    assert (500, 20) in rows and (600, 20) in rows


async def test_rerun_is_idempotent(db_session):
    _user(db_session, 500)
    _tag(db_session, 20, TagType.SOURCE, "S")
    for i in (1, 2, 3):
        _img(db_session, i)
        _link(db_session, 20, i)
    await db_session.flush()  # see comment in test_lift_and_affinity_from_favorites
    for i in (1, 2, 3):
        _fav(db_session, 500, i)
    await db_session.commit()

    n1 = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    n2 = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
    assert n1 == n2 > 0
    rows = await _rows(db_session)
    assert rows[(500, 20)].pool_cnt == 3


async def test_lock_skip_returns_sentinel(db_session, engine):
    # A second connection (from the shared per-test `engine`) holds the lock
    # -> refresh skips with -1. MySQL named locks are connection-scoped, so a
    # fresh connection() checkout from the same engine is a distinct session.
    from sqlalchemy import text as sqla_text

    db_name = (await db_session.execute(sqla_text("SELECT DATABASE()"))).scalar()
    lock_name = f"{_LOCK_PREFIX}:{db_name}"
    async with engine.connect() as other:
        got = (await other.execute(sqla_text("SELECT GET_LOCK(:n, 0)"), {"n": lock_name})).scalar()
        assert got == 1
        n = await refresh_user_tag_affinity(db_session, **REFRESH_KW)
        assert n == -1
        await other.execute(sqla_text("SELECT RELEASE_LOCK(:n)"), {"n": lock_name})

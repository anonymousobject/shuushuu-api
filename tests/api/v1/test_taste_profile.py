import pytest

from app.config import TagType
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.api]


def _aff(
    db,
    user_id,
    tag_id,
    *,
    pool_cnt=0,
    fav=0,
    upl=0,
    rated=0,
    rating_avg=None,
    lift=None,
    delta=None,
    affinity=0.0,
):
    db.add(
        UserTagAffinity(
            user_id=user_id,
            tag_id=tag_id,
            pool_cnt=pool_cnt,
            fav_count=fav,
            upload_count=upl,
            rated_count=rated,
            rating_avg=rating_avg,
            lift=lift,
            rating_delta=delta,
            affinity=affinity,
        )
    )


async def test_requires_auth(client):
    resp = await client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 401


async def test_cold_start_profile_not_ready(authenticated_client):
    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is False
    assert data["top_tags"] == []
    assert data["rated_high"] == []
    assert data["rated_low"] == []


async def test_profile_payload(db_session, authenticated_client, sample_user):
    uid = sample_user.user_id
    db_session.add(Tags(tag_id=201, type=TagType.SOURCE, title="Code Geass"))
    db_session.add(Tags(tag_id=202, type=TagType.THEME, title="long hair"))
    db_session.add(Tags(tag_id=203, type=TagType.THEME, title="chibi"))
    # strong positive: high lift + positive delta
    _aff(
        db_session,
        uid,
        201,
        pool_cnt=100,
        fav=90,
        upl=20,
        rated=50,
        rating_avg=9.0,
        lift=12.0,
        delta=2.0,
        affinity=3.5,
    )
    # popularity-only: lift below the 1.5 display floor -> excluded from top_tags
    _aff(db_session, uid, 202, pool_cnt=500, fav=500, lift=1.3, affinity=0.26)
    # disliked: negative delta -> rated_low
    _aff(db_session, uid, 203, rated=30, rating_avg=5.0, lift=None, delta=-2.0, affinity=-1.0)

    # summary.pool_size/rated_total/mean_rating are live-queried from
    # favorites/images/image_ratings (not the precomputed user_tag_affinity
    # rows above), so they need real rows. Images 9001/9002 are someone
    # else's (user_id=1, seeded by conftest); 9003 is sample_user's own
    # upload, also favorited -- pool_size dedupes it to one entry, not two.
    # Flush the images before adding Favorites/ImageRatings: SQLAlchemy
    # doesn't order composite-PK, relationship-less inserts after their FK
    # targets within a single flush (see the writeup in
    # tests/services/test_user_tag_affinity.py).
    db_session.add(Images(image_id=9001, user_id=1, ext="jpg"))
    db_session.add(Images(image_id=9002, user_id=1, ext="jpg"))
    db_session.add(Images(image_id=9003, user_id=uid, ext="jpg"))
    await db_session.flush()
    db_session.add(Favorites(user_id=uid, image_id=9001))
    db_session.add(Favorites(user_id=uid, image_id=9002))
    db_session.add(Favorites(user_id=uid, image_id=9003))  # favorite of own upload; dedupes
    db_session.add(ImageRatings(user_id=uid, image_id=9001, rating=8))
    db_session.add(ImageRatings(user_id=uid, image_id=9002, rating=6))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is True
    top_ids = [t["tag_id"] for t in data["top_tags"]]
    assert top_ids == [201]  # 202 fails the lift floor; 203 has no pool support
    top = data["top_tags"][0]
    assert top["title"] == "Code Geass"
    assert top["type_name"] == "Source"
    assert top["lift"] == pytest.approx(12.0)
    high_ids = [t["tag_id"] for t in data["rated_high"]]
    low_ids = [t["tag_id"] for t in data["rated_low"]]
    assert high_ids == [201]  # only positive deltas
    assert low_ids == [203]  # only negative deltas
    assert data["summary"]["pool_size"] == 3  # 9001, 9002, 9003 deduped
    assert data["summary"]["rated_total"] == 2
    assert data["summary"]["mean_rating"] == pytest.approx(7.0)  # (8 + 6) / 2


async def test_other_users_rows_not_leaked(db_session, authenticated_client, sample_user):
    db_session.add(Tags(tag_id=201, type=TagType.SOURCE, title="S"))
    _aff(db_session, sample_user.user_id + 1, 201, pool_cnt=100, lift=10.0, affinity=2.3)
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/users/me/taste-profile")
    assert resp.json()["profile_ready"] is False

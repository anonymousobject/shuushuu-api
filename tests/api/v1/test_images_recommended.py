import pytest

from app.config import ImageStatus, TagType
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_rating import ImageRatings
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.api]


def _img(db, image_id, user_id, status=ImageStatus.ACTIVE):
    db.add(Images(image_id=image_id, user_id=user_id, ext="jpg", status=status))


def _aff(db, user_id, tag_id, affinity, pool_cnt=10):
    db.add(
        UserTagAffinity(
            user_id=user_id,
            tag_id=tag_id,
            pool_cnt=pool_cnt,
            fav_count=pool_cnt,
            upload_count=0,
            rated_count=0,
            rating_avg=None,
            lift=5.0 if affinity > 0 else None,
            rating_delta=None,
            affinity=affinity,
        )
    )


@pytest.fixture
async def rec_world(db_session, sample_user, test_user):
    """3 images by test_user; sample_user loves tag 301 (+2), dislikes 302 (−3)."""
    db_session.add(Tags(tag_id=301, type=TagType.SOURCE, title="Loved"))
    db_session.add(Tags(tag_id=302, type=TagType.THEME, title="Hated"))
    _img(db_session, 9001, test_user.user_id)  # tags: 301        -> score +2
    _img(db_session, 9002, test_user.user_id)  # tags: 301, 302   -> score −1
    _img(db_session, 9003, test_user.user_id)  # tags: 302        -> not a candidate
    await db_session.flush()
    for iid, tags in [(9001, [301]), (9002, [301, 302]), (9003, [302])]:
        for t in tags:
            db_session.add(TagLinks(tag_id=t, image_id=iid, user_id=test_user.user_id))
    _aff(db_session, sample_user.user_id, 301, 2.0)
    _aff(db_session, sample_user.user_id, 302, -3.0)
    await db_session.commit()
    return sample_user


async def test_requires_auth(client):
    resp = await client.get("/api/v1/images/recommended")
    assert resp.status_code == 401


async def test_cold_start(authenticated_client):
    resp = await authenticated_client.get("/api/v1/images/recommended")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is False
    assert data["images"] == []
    assert data["total"] == 0


async def test_scored_order_and_because_tags(authenticated_client, rec_world):
    resp = await authenticated_client.get("/api/v1/images/recommended")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is True
    ids = [im["image_id"] for im in data["images"]]
    # 9001 (+2.0) ranks above 9002 (2.0 − 3.0 = −1.0); 9003 never becomes a
    # candidate (only carries a negative-affinity tag).
    assert ids == [9001, 9002]
    because = data["images"][0]["because_tags"]
    assert [t["tag_id"] for t in because] == [301]
    assert because[0]["title"] == "Loved"


async def test_candidate_includes_alias_only_tagged_image(
    db_session, authenticated_client, rec_world, test_user
):
    """An image tagged ONLY through an alias of a top-affinity canonical tag
    must still surface as a candidate. The candidate subquery filters
    `tl.tag_id IN top_tag_ids` directly (no alias resolution) for index
    reasons, so a naive implementation only ever finds canonical-tagged
    images even though scoring resolves aliases fine."""
    db_session.add(Tags(tag_id=303, type=TagType.SOURCE, title="Loved Alias", alias_of=301))
    _img(db_session, 9008, test_user.user_id)  # tagged only via alias 303 -> score +2
    await db_session.flush()
    db_session.add(TagLinks(tag_id=303, image_id=9008, user_id=test_user.user_id))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    data = resp.json()
    ids = [im["image_id"] for im in data["images"]]
    # 9008 ties 9001 at score +2.0; ORDER BY score DESC, image_id DESC puts
    # the higher id first among ties.
    assert ids == [9008, 9001, 9002]
    img = next(im for im in data["images"] if im["image_id"] == 9008)
    because = img["because_tags"]
    assert [t["tag_id"] for t in because] == [301]  # resolves to the canonical tag
    assert because[0]["title"] == "Loved"


async def test_excludes_seen_and_own_images(db_session, authenticated_client, rec_world):
    uid = rec_world.user_id
    db_session.add(Favorites(user_id=uid, image_id=9001))  # favorited -> excluded
    db_session.add(ImageRatings(user_id=uid, image_id=9002, rating=8))  # rated -> excluded
    _img(db_session, 9004, uid)  # own upload -> excluded
    await db_session.flush()
    db_session.add(TagLinks(tag_id=301, image_id=9004, user_id=uid))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert ids == []


async def test_excludes_hidden_statuses(db_session, authenticated_client, rec_world, test_user):
    _img(db_session, 9005, test_user.user_id, status=ImageStatus.DEACTIVATED)
    await db_session.flush()
    db_session.add(TagLinks(tag_id=301, image_id=9005, user_id=test_user.user_id))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert 9005 not in ids


async def test_hide_reposts_setting(db_session, authenticated_client, rec_world, test_user):
    _img(db_session, 9006, test_user.user_id, status=ImageStatus.REPOST)
    await db_session.flush()
    db_session.add(TagLinks(tag_id=301, image_id=9006, user_id=test_user.user_id))
    await db_session.commit()

    # hide_reposts=0 (default): REPOST is a public status -> visible
    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert 9006 in ids

    rec_world.hide_reposts = 1
    db_session.add(rec_world)
    await db_session.commit()

    # hide_reposts=1 -> repost excluded even though it carries a loved tag
    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert 9006 not in ids


async def test_show_all_images_includes_deactivated(
    db_session, authenticated_client, rec_world, test_user
):
    rec_world.show_all_images = 1
    db_session.add(rec_world)
    _img(db_session, 9007, test_user.user_id, status=ImageStatus.DEACTIVATED)
    await db_session.flush()
    db_session.add(TagLinks(tag_id=301, image_id=9007, user_id=test_user.user_id))
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    ids = [im["image_id"] for im in resp.json()["images"]]
    assert 9007 in ids


async def test_profile_with_only_negative_affinity(db_session, authenticated_client, sample_user):
    """A profile exists but has no positive-affinity tags -> ready, but no candidates."""
    _aff(db_session, sample_user.user_id, 302, -3.0)
    await db_session.commit()

    resp = await authenticated_client.get("/api/v1/images/recommended")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_ready"] is True
    assert data["images"] == []
    assert data["total"] == 0


async def test_pagination_slices_scored_list(authenticated_client, rec_world):
    resp = await authenticated_client.get("/api/v1/images/recommended?page=2&per_page=1")
    data = resp.json()
    assert data["total"] == 2
    assert [im["image_id"] for im in data["images"]] == [9002]

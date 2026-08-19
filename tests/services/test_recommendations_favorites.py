import pytest

from app.config import ImageStatus, TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user_favorite import UserFavoriteLinks, UserFavoriteTags
from app.services.recommendations import load_favorite_pools

pytestmark = [pytest.mark.api]

CHAR = 9701
SRC = 9702
ARTIST = 9703
SRC_ALIAS = 9704  # alias_of=SRC


def _img(db, image_id, user_id, status=ImageStatus.ACTIVE):
    db.add(Images(image_id=image_id, user_id=user_id, ext="jpg", status=status))


@pytest.fixture
async def fav_world(db_session, sample_user, test_user):
    """sample_user favorites: combo (CHAR, SRC) pos 0, source SRC pos 0, artist ARTIST pos 0.

    Images (all by test_user):
      9601 CHAR+SRC        -> combo + source match
      9602 SRC only        -> source match only
      9603 CHAR only       -> matches nothing (combo needs both tags)
      9604 SRC_ALIAS only  -> source match via alias expansion
      9605 ARTIST          -> artist match
      9606 SRC, DEACTIVATED -> filtered by visibility
      9607 SRC, favorited by sample_user -> filtered as seen
    """
    db_session.add(Tags(tag_id=CHAR, type=TagType.CHARACTER, title="fav char"))
    db_session.add(Tags(tag_id=SRC, type=TagType.SOURCE, title="fav source"))
    db_session.add(Tags(tag_id=ARTIST, type=TagType.ARTIST, title="fav artist"))
    db_session.add(Tags(tag_id=SRC_ALIAS, type=TagType.SOURCE, title="alias", alias_of=SRC))
    for iid, _tags in [
        (9601, [CHAR, SRC]),
        (9602, [SRC]),
        (9603, [CHAR]),
        (9604, [SRC_ALIAS]),
        (9605, [ARTIST]),
        (9607, [SRC]),
    ]:
        _img(db_session, iid, test_user.user_id)
    _img(db_session, 9606, test_user.user_id, status=ImageStatus.DEACTIVATED)
    await db_session.flush()
    for iid, tags in [
        (9601, [CHAR, SRC]),
        (9602, [SRC]),
        (9603, [CHAR]),
        (9604, [SRC_ALIAS]),
        (9605, [ARTIST]),
        (9606, [SRC]),
        (9607, [SRC]),
    ]:
        for t in tags:
            db_session.add(TagLinks(tag_id=t, image_id=iid, user_id=test_user.user_id))
    link = CharacterSourceLinks(character_tag_id=CHAR, source_tag_id=SRC)
    db_session.add(link)
    await db_session.flush()
    assert link.id is not None
    db_session.add(UserFavoriteLinks(user_id=sample_user.user_id, link_id=link.id, position=0))
    db_session.add(UserFavoriteTags(user_id=sample_user.user_id, tag_id=SRC, position=0))
    db_session.add(UserFavoriteTags(user_id=sample_user.user_id, tag_id=ARTIST, position=0))
    db_session.add(Favorites(user_id=sample_user.user_id, image_id=9607))
    await db_session.commit()
    return sample_user


async def test_pool_order_matching_and_filtering(db_session, fav_world):
    pools = await load_favorite_pools(db_session, fav_world, cap=100)
    assert len(pools) == 3
    combo, source, artist = pools
    # combos first, then sources, then artists
    assert combo.attribution.character.tag_id == CHAR
    assert combo.attribution.source.tag_id == SRC
    assert source.attribution.tag.tag_id == SRC
    assert artist.attribution.tag.tag_id == ARTIST
    # combo needs BOTH tags: 9603 (char only) matches nothing
    assert combo.image_ids == [9601]
    # source matches direct, combo-carried, and alias-tagged images, newest-first;
    # 9606 (hidden) and 9607 (already favorited) are filtered out
    assert source.image_ids == [9604, 9602, 9601]
    assert artist.image_ids == [9605]


async def test_no_favorites_returns_empty(db_session, sample_user):
    assert await load_favorite_pools(db_session, sample_user, cap=100) == []


async def test_cap_limits_recall_before_filtering(db_session, fav_world):
    pools = await load_favorite_pools(db_session, fav_world, cap=3)
    source = next(p for p in pools if p.attribution.tag and p.attribution.tag.tag_id == SRC)
    # cap applies at recall, newest-first: the top-3 recent SRC matches are
    # 9607 (seen), 9606 (hidden), 9604 — filtering then thins the capped list,
    # it does not backfill. This is the spec's accepted cap-before-filter shape.
    assert source.image_ids == [9604]


async def test_own_uploads_excluded(db_session, fav_world, test_user):
    pools = await load_favorite_pools(db_session, test_user, cap=100)
    assert pools == []  # test_user has no favorites…
    db_session.add(UserFavoriteTags(user_id=test_user.user_id, tag_id=SRC, position=0))
    await db_session.commit()
    pools = await load_favorite_pools(db_session, test_user, cap=100)
    # …and every SRC image is test_user's own upload -> all filtered
    assert pools[0].image_ids == []


async def test_hide_reposts_respected(db_session, fav_world, test_user):
    _img(db_session, 9608, test_user.user_id, status=ImageStatus.REPOST)
    await db_session.flush()
    db_session.add(TagLinks(tag_id=SRC, image_id=9608, user_id=test_user.user_id))
    fav_world.hide_reposts = 1
    db_session.add(fav_world)
    await db_session.commit()
    pools = await load_favorite_pools(db_session, fav_world, cap=100)
    source = next(p for p in pools if p.attribution.tag and p.attribution.tag.tag_id == SRC)
    assert 9608 not in source.image_ids


TIE_LOW = 9705
TIE_HIGH = 9706


async def test_pool_order_deterministic_when_positions_tie(db_session, sample_user):
    """position is non-unique (the add flow accepts a same-position race, see
    app/api/v1/users.py's _apply_tag doc comment); tag_id must break the tie
    so pool order is total and repeated calls agree.
    """
    db_session.add(Tags(tag_id=TIE_HIGH, type=TagType.SOURCE, title="tie high"))
    db_session.add(Tags(tag_id=TIE_LOW, type=TagType.SOURCE, title="tie low"))
    # Inserted high-id-first to show insertion order isn't what decides this.
    db_session.add(UserFavoriteTags(user_id=sample_user.user_id, tag_id=TIE_HIGH, position=0))
    db_session.add(UserFavoriteTags(user_id=sample_user.user_id, tag_id=TIE_LOW, position=0))
    await db_session.commit()

    first = await load_favorite_pools(db_session, sample_user, cap=100)
    second = await load_favorite_pools(db_session, sample_user, cap=100)

    order = [p.attribution.tag.tag_id for p in first]
    assert order == [TIE_LOW, TIE_HIGH]
    assert [p.attribution.tag.tag_id for p in second] == order

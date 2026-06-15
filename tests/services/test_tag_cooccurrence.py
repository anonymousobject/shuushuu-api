import pytest
from sqlalchemy import text

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.services.tag_cooccurrence import refresh_tag_cooccurrence

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


def _img(db, image_id, status=ImageStatus.ACTIVE):
    # ext is NOT NULL with no default in the schema, so it must be supplied.
    db.add(Images(image_id=image_id, user_id=1, ext="jpg", status=status))


def _tag(db, tag_id, ttype, title, alias_of=None, usage_count=0):
    db.add(Tags(tag_id=tag_id, type=ttype, title=title, alias_of=alias_of, usage_count=usage_count))


def _link(db, tag_id, image_id):
    db.add(TagLinks(tag_id=tag_id, image_id=image_id, user_id=1))


async def _rows(db):
    res = await db.execute(
        text(
            "SELECT tag_id, related_tag_id, related_type, cooccur_count, lift, confidence "
            "FROM tag_cooccurrence"
        )
    )
    return {(r.tag_id, r.related_tag_id): r for r in res.all()}


async def test_lift_ranks_real_association_above_generic(db_session):
    # B=char(10) on imgs 1..4; A1=source(20) on 1..4 (tight); A2=theme(30) everywhere, co-occurs 4/4
    _tag(db_session, 10, TagType.CHARACTER, "Reimu")
    _tag(db_session, 20, TagType.SOURCE, "Touhou")
    _tag(db_session, 30, TagType.THEME, "solo")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    for i in range(5, 100):
        _img(db_session, i)
    for i in range(1, 100):
        _link(db_session, 30, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert rows[(10, 20)].lift > rows[(10, 30)].lift  # Touhou beats generic "solo"


async def test_min_support_drops_rare_pairs(db_session):
    # (10,20) co-occur on 3 images; (10,30) co-occur on only 1 image.
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    _tag(db_session, 30, TagType.THEME, "C")
    for i in range(1, 4):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    _img(db_session, 4)
    _link(db_session, 10, 4)
    _link(db_session, 30, 4)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=3, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert (10, 20) in rows  # c == 3 >= min_cooccur
    assert (20, 10) in rows
    assert (10, 30) not in rows  # c == 1 < min_cooccur, dropped
    assert (30, 10) not in rows


async def test_confidence_is_cooccur_over_base_visible_count(db_session):
    # 10 on imgs 1..4; 20 on imgs 1..4. conf(10->20) = c/vc(10) = 4/4 = 1.0
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert rows[(10, 20)].confidence == pytest.approx(1.0)
    assert rows[(10, 20)].cooccur_count == 4


async def test_directional_rows_both_present(db_session):
    # 10 on imgs 1..4 (vc=4); 20 on imgs 1..2 (vc=2). c(10,20) = 2.
    # conf(10->20) = 2/4 = 0.5; conf(20->10) = 2/2 = 1.0 -> both present, differ.
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
    for i in range(1, 3):
        _link(db_session, 20, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert (10, 20) in rows
    assert (20, 10) in rows
    assert rows[(10, 20)].confidence == pytest.approx(0.5)
    assert rows[(20, 10)].confidence == pytest.approx(1.0)


async def test_top_n_truncates_per_base(db_session):
    # base 10 co-occurs with 20 (every image) and 30 (half the images).
    # 10 on 1..4; 20 on 1..4 (c=4, lift high); 30 on 1..2 (c=2, lift lower).
    # top_n=1 -> base 10 keeps only its single best neighbour (20).
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    _tag(db_session, 30, TagType.THEME, "C")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    for i in range(1, 3):
        _link(db_session, 30, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=1, min_base_usage=0)
    rows = await _rows(db_session)
    base10 = [k for k in rows if k[0] == 10]
    assert base10 == [(10, 20)]  # only the single best neighbour kept


async def test_aliases_resolved_to_canonical(db_session):
    # 21 is an alias of canonical 20. Links to 21 must count toward 20.
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "Canonical")
    _tag(db_session, 21, TagType.SOURCE, "Alias", alias_of=20)
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 21, i)  # links go to the ALIAS
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert (10, 20) in rows  # resolved to canonical
    assert (10, 21) not in rows  # alias id never appears
    assert (21, 10) not in rows
    assert rows[(10, 20)].cooccur_count == 4


async def test_deactivated_images_excluded(db_session):
    # 10 & 20 co-occur on 4 visible images plus 2 DEACTIVATED ones.
    # Only the 4 visible co-occurrences count.
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    for i in range(5, 7):
        _img(db_session, i, status=ImageStatus.DEACTIVATED)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert rows[(10, 20)].cooccur_count == 4  # the 2 deactivated images don't contribute
    assert rows[(10, 20)].confidence == pytest.approx(1.0)  # vc(10) == 4, not 6


async def test_self_pairs_excluded(db_session):
    # A single tag on several images must never produce an (X, X) self row.
    _tag(db_session, 10, TagType.CHARACTER, "A")
    _tag(db_session, 20, TagType.SOURCE, "B")
    for i in range(1, 5):
        _img(db_session, i)
        _link(db_session, 10, i)
        _link(db_session, 20, i)
    await db_session.commit()

    await refresh_tag_cooccurrence(db_session, min_cooccur=2, top_n=50, min_base_usage=0)
    rows = await _rows(db_session)
    assert (10, 10) not in rows
    assert (20, 20) not in rows

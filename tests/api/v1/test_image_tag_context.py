"""context_source_tag_id stamping on image tag lists (compound-search rule)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks


def _tag(db, tag_id, ttype, title, alias_of=None):
    db.add(Tags(tag_id=tag_id, type=ttype, title=title, alias_of=alias_of))


def _img(db, image_id):
    # ext is NOT NULL with no default; user 1 is pre-seeded by conftest.
    db.add(Images(image_id=image_id, user_id=1, ext="jpg", status=ImageStatus.ACTIVE))


def _link(db, tag_id, image_id):
    db.add(TagLinks(tag_id=tag_id, image_id=image_id, user_id=1))


async def _setup(db: AsyncSession) -> None:
    # Sources S1 (801), S2 (802), alias of S2 (803); characters: C-both (811)
    # linked to S1+S2, C-one (812) linked to S1 only, C-none (813) unlinked.
    _tag(db, 801, TagType.SOURCE, "ctx src one")
    _tag(db, 802, TagType.SOURCE, "ctx src two")
    _tag(db, 803, TagType.SOURCE, "ctx src two alias", alias_of=802)
    _tag(db, 811, TagType.CHARACTER, "ctx char both")
    _tag(db, 812, TagType.CHARACTER, "ctx char one")
    _tag(db, 813, TagType.CHARACTER, "ctx char none")
    # Flush so the tag rows exist before the link references them: CharacterSourceLinks
    # intentionally omits an ORM relationship() to Tags (see
    # app/models/character_source_link.py), so unit-of-work has no dependency edge to
    # order the two mappers' inserts on its own.
    await db.flush()
    db.add(CharacterSourceLinks(character_tag_id=811, source_tag_id=801))
    db.add(CharacterSourceLinks(character_tag_id=811, source_tag_id=802))
    db.add(CharacterSourceLinks(character_tag_id=812, source_tag_id=801))
    # 8001: C-one + S1            -> exactly one linked present -> stamp 801
    _img(db, 8001)
    _link(db, 812, 8001)
    _link(db, 801, 8001)
    # 8002: C-both + S1 + S2      -> two linked present -> None
    _img(db, 8002)
    _link(db, 811, 8002)
    _link(db, 801, 8002)
    _link(db, 802, 8002)
    # 8003: C-one, no source      -> None
    _img(db, 8003)
    _link(db, 812, 8003)
    # 8004: C-both + S2-ALIAS only -> resolves to canonical 802 -> stamp 802
    _img(db, 8004)
    _link(db, 811, 8004)
    _link(db, 803, 8004)
    await db.commit()


def _ctx(payload: dict, tag_id: int):
    by_id = {t["tag_id"]: t for t in payload["tags"]}
    return by_id[tag_id]["context_source_tag_id"]


async def test_detail_stamps_exactly_one(client: AsyncClient, db_session: AsyncSession):
    await _setup(db_session)
    r = await client.get("/api/v1/images/8001")
    assert r.status_code == 200
    assert _ctx(r.json(), 812) == 801
    # source entries are never stamped
    assert _ctx(r.json(), 801) is None


async def test_detail_ambiguous_and_sourceless_are_none(client, db_session):
    await _setup(db_session)
    assert _ctx((await client.get("/api/v1/images/8002")).json(), 811) is None
    assert _ctx((await client.get("/api/v1/images/8003")).json(), 812) is None


async def test_detail_alias_source_resolves_to_canonical(client, db_session):
    await _setup(db_session)
    assert _ctx((await client.get("/api/v1/images/8004")).json(), 811) == 802


async def test_list_endpoint_stamps(client, db_session):
    await _setup(db_session)
    r = await client.get("/api/v1/images/", params={"tags": "812", "per_page": 50})
    assert r.status_code == 200
    payload = r.json()
    imgs = {i["image_id"]: i for i in payload["images"]}
    assert _ctx(imgs[8001], 812) == 801
    assert _ctx(imgs[8003], 812) is None

"""Tests for the --conflated report mode of scripts/analyze_character_sources.py."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from scripts.analyze_character_sources import find_conflated_characters


def _tag(db, tag_id, ttype, title, usage_count=0, alias_of=None):
    db.add(Tags(tag_id=tag_id, type=ttype, title=title, usage_count=usage_count, alias_of=alias_of))


def _img(db, image_id):
    # Images.ext is NOT NULL with no default; user 1 is pre-seeded by conftest.
    db.add(Images(image_id=image_id, user_id=1, ext="jpg"))


def _link(db, tag_id, image_id):
    db.add(TagLinks(tag_id=tag_id, image_id=image_id, user_id=1))


async def test_conflated_detection(db_session: AsyncSession):
    # Sources: S1 (901), S2 (902), and an alias of S2 (903).
    _tag(db_session, 901, TagType.SOURCE, "Series One")
    _tag(db_session, 902, TagType.SOURCE, "Series Two")
    _tag(db_session, 903, TagType.SOURCE, "Series Two Alias", alias_of=902)

    # A: conflated — 30 images: 20 on S1, 10 on S2 (4 of them via the alias).
    _tag(db_session, 911, TagType.CHARACTER, "Conflated", usage_count=30)
    # B: single-source — 30 images, all S1.
    _tag(db_session, 912, TagType.CHARACTER, "SingleSource", usage_count=30)
    # C: split sources but usage below min_usage.
    _tag(db_session, 913, TagType.CHARACTER, "BelowUsage", usage_count=10)

    image_id = 9000

    def add_image() -> int:
        nonlocal image_id
        image_id += 1
        _img(db_session, image_id)
        return image_id

    for i in range(30):
        iid = add_image()
        _link(db_session, 911, iid)
        if i < 20:
            _link(db_session, 901, iid)  # S1
        elif i < 26:
            _link(db_session, 902, iid)  # S2 directly
        else:
            _link(db_session, 903, iid)  # S2 via alias
    for _ in range(30):
        iid = add_image()
        _link(db_session, 912, iid)
        _link(db_session, 901, iid)
    for i in range(10):
        iid = add_image()
        _link(db_session, 913, iid)
        _link(db_session, 901 if i % 2 else 902, iid)

    # Flush so the tag rows exist before the link references them: CharacterSourceLinks
    # intentionally omits an ORM relationship() to Tags (see app/models/character_source_link.py),
    # so unit-of-work has no dependency edge to order the two mappers' inserts on its own.
    await db_session.flush()

    # A already has one link (to S1): reported, not excluded.
    db_session.add(CharacterSourceLinks(character_tag_id=911, source_tag_id=901))
    await db_session.commit()

    results = await find_conflated_characters(
        db_session, min_usage=20, min_images=5, min_share=0.15
    )

    assert [r["character_tag_id"] for r in results] == [911]
    row = results[0]
    assert row["character_title"] == "Conflated"
    assert row["total_character_images"] == 30
    assert row["source_tagged_images"] == 30
    assert row["linked_count"] == 1
    # Alias-tagged images roll up into the canonical source; count-descending.
    assert [(s["source_tag_id"], s["count"]) for s in row["sources"]] == [(901, 20), (902, 10)]
    assert row["sources"][0]["source_title"] == "Series One"
    assert row["sources"][1]["source_title"] == "Series Two"

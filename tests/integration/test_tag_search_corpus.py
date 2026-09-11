"""Acceptance corpus for Postgres tag search (spec appendix).

Seeds the titles behind every query in the design doc's appendix, with the
decoys that broke earlier ranking attempts, and asserts the top hit by title.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks
from app.services.tag_search import search_tags

# (title, type, usage_count, desc, alias_of_title, urls)
CORPUS = [
    ("cherry blossoms", TagType.THEME, 21476, "", None, []),
    ("sakura", TagType.THEME, 0, "", "cherry blossoms", []),
    ("Sakura", TagType.CHARACTER, 797, "", None, []),
    ("Sakura (source)", TagType.SOURCE, 3, "", None, []),
    ("Kinomoto Sakura", TagType.CHARACTER, 4594, "Cardcaptor Sakura", None, []),
    ("Kinoshita Sakura", TagType.CHARACTER, 41, "", None, []),
    ("Kinomoto Sakuya", TagType.CHARACTER, 10, "", None, []),
    ("Kinomoto Touya", TagType.CHARACTER, 94, "", None, []),
    ("Kinomoto Nadeshiko", TagType.CHARACTER, 27, "", None, []),
    ("Sakurai Yukino", TagType.CHARACTER, 28, "", None, []),
    ("Sakura Yukino", TagType.CHARACTER, 18, "", None, []),
    ("Sakurakinoshita Ashita", TagType.CHARACTER, 14, "", None, []),
    ("Kinom", TagType.ARTIST, 8, "", None, []),
    ("Kinoto", TagType.ARTIST, 2, "", None, []),
    ("Kino", TagType.CHARACTER, 504, "", None, []),
    ("cat", TagType.THEME, 18416, "", None, []),
    ("neko", TagType.THEME, 0, "", "cat", []),
    ("Neko", TagType.CHARACTER, 10, "", None, []),
    ("neko mimi", TagType.THEME, 5000, "", None, []),
    ("Nekopara", TagType.SOURCE, 100, "", None, []),
    ("maid", TagType.THEME, 23093, "", None, []),
    ("school uniform", TagType.THEME, 154188, "", None, []),
    ("school bag", TagType.THEME, 10191, "", None, []),
    ("school swimsuit", TagType.THEME, 6409, "", None, []),
    ("School Days", TagType.SOURCE, 900, "", None, []),
    ("swimsuit", TagType.THEME, 8829, "", None, []),
    ("mizugi", TagType.THEME, 0, "", "swimsuit", []),
    ("bikini", TagType.THEME, 41801, "", None, []),
    ("two-piece swimsuit", TagType.THEME, 0, "", "bikini", []),
    ("Swim Swim", TagType.CHARACTER, 23, "", None, []),
    ("long hair", TagType.THEME, 729274, "", None, []),
    ("short hair", TagType.THEME, 474227, "", None, []),
    ("blonde hair", TagType.THEME, 279469, "", None, []),
    ("Somali Longhaired", TagType.CHARACTER, 1, "", None, []),
    ("Shiroko Terror", TagType.CHARACTER, 42, "Blue Archive; long hair variant", None, []),
    ("long", TagType.ARTIST, 1, "", None, []),
    ("long kimono", TagType.THEME, 40260, "", None, []),
    ("Sa", TagType.ARTIST, 1, "", None, []),
    ("Sa.", TagType.ARTIST, 9, "", None, []),
    ("sad", TagType.THEME, 5000, "", None, []),
    ("Saber", TagType.CHARACTER, 20000, "", None, []),
    ("co", TagType.ARTIST, 4, "", None, []),
    ("cosplay", TagType.THEME, 3000, "", None, []),
    ("Hatsune Miku", TagType.CHARACTER, 35622, "", None, []),
    ("Hatsune Mikuo", TagType.CHARACTER, 513, "", None, []),
    ("Hatsune", TagType.ARTIST, 10, "", None, []),
    ("thigh highs", TagType.THEME, 162274, "", None, []),
    ("boots", TagType.THEME, 90000, "Includes thigh-high boots", None, []),
    ("C.C.", TagType.CHARACTER, 2788, "", None, []),
    ("C++", TagType.THEME, 5, "", None, []),
    ("C++ 11", TagType.THEME, 1, "", None, []),
    ("[C]", TagType.SOURCE, 138, "", None, []),
    ("100", TagType.ARTIST, 7, "", None, []),
    ("100% Perfect Girl", TagType.SOURCE, 3, "", None, []),
    ("100% Orange Juice!", TagType.SOURCE, 1, "", None, []),
    ("Ichigo 100%", TagType.SOURCE, 47, "", None, []),
    ("Mob Psycho 100", TagType.SOURCE, 26, "", None, []),
    ("Pixiv 103175", TagType.ARTIST, 1, "", None, []),
    ("Deep-Blue Series", TagType.SOURCE, 50, "", None, []),
    ("Deep Blue Sky & Pure White Wings", TagType.SOURCE, 92, "", None, []),
    ("Yano (yano_0o0)", TagType.ARTIST, 1, "", None, []),
    ("Yano Mitsuki", TagType.CHARACTER, 30, "", None, []),
    ("The Forgotten Field", TagType.SOURCE, 1, "", None, []),
    ("The Familiar of Zero", TagType.SOURCE, 607, "", None, []),
    ("The Fly", TagType.CHARACTER, 4, "", None, []),
    ("The Forest of Drizzling Rain", TagType.SOURCE, 437, "", None, []),
    ("THE", TagType.ARTIST, 6, "", None, []),
    ("TKennshou", TagType.ARTIST, 1, "", None, ["https://www.pixiv.net/users/21412050"]),
    ("Pixiv 21412050", TagType.ARTIST, 0, "", "TKennshou", []),
    ("Tsunekichi", TagType.ARTIST, 14, "", None, []),
    ("Pokémon", TagType.SOURCE, 16529, "", None, []),
    ("Pokémon Adventures", TagType.SOURCE, 3279, "", None, []),
    ("Pokemon Heroes", TagType.SOURCE, 12, "", None, []),
    ("Märchen von Friedhof", TagType.CHARACTER, 1047, "", None, []),
    ("Marchen Girl Runs", TagType.SOURCE, 3, "", None, []),
    ("MarchAB", TagType.ARTIST, 41, "", None, []),
    ("Maruchan", TagType.ARTIST, 68, "", None, []),
    ("EB十", TagType.ARTIST, 152, "", None, []),
    ("magical girl", TagType.THEME, 25205, "", None, []),
    ("Mahou no Stage Fancy Lala", TagType.SOURCE, 100, "magical girl idol anime", None, []),
    ("Louise Françoise le Blanc de la Vallière", TagType.CHARACTER, 447, "", None, []),
    ("Francoise", TagType.CHARACTER, 3, "", None, []),
    ("Claire Francois", TagType.CHARACTER, 50, "", None, []),
]

# (query, expected top-1 title) — spec appendix, asserted by title.
EXPECTED_TOP1 = [
    ("sakura", "sakura"),
    ("cat", "cat"),
    ("maid", "maid"),
    ("neko", "neko"),
    ("school", "school uniform"),
    ("swimsuit", "swimsuit"),
    ("long hair", "long hair"),
    ("sakura kinomoto", "Kinomoto Sakura"),
    ("kinomoto sakura", "Kinomoto Sakura"),
    ("sa", "Sa"),
    ("co", "co"),
    ("long", "long"),
    ("kinomto", "Kinomoto Sakura"),
    ("sakrua kinomto", "Kinomoto Sakura"),
    ("hatsune mikuu", "Hatsune Miku"),
    ("swimsiut", "swimsuit"),
    ("thig", "thigh highs"),
    ("C.C.", "C.C."),
    ("C++", "C++"),
    ("100%", "100% Perfect Girl"),
    ("deep-blue", "Deep-Blue Series"),
    ("yano_0o0", "Yano (yano_0o0)"),
    ("The Forgotten", "The Forgotten Field"),
    ("The F", "The Familiar of Zero"),
    ("21412050", "Pixiv 21412050"),
    ("pixiv.net/users/21412050", "Pixiv 21412050"),
    ("tsunekichi", "Tsunekichi"),
    ("pokemon", "Pokémon"),
    ("Pokémon", "Pokémon"),
    ("marchen", "Märchen von Friedhof"),
    ("EB十", "EB十"),
    ("magical girl", "magical girl"),
    ("Louise Francoise", "Louise Françoise le Blanc de la Vallière"),
    ("marchan", "Märchen von Friedhof"),
    ("kinomoto", "Kinomoto Sakura"),
    ("hatsune", "Hatsune"),
    ("sakura kino", "Kinomoto Sakura"),
    ("kinomt", "Kinomoto Sakura"),
]


async def seed_corpus(db_session: AsyncSession) -> dict[str, Tags]:
    """Insert the corpus; aliases are wired by title after the first insert."""
    by_title: dict[str, Tags] = {}
    for title, tag_type, usage, desc, _alias, _urls in CORPUS:
        tag = Tags(title=title, type=tag_type, usage_count=usage, desc=desc or None)
        db_session.add(tag)
        by_title[title] = tag
    await db_session.flush()
    for title, _tag_type, _usage, _desc, alias_of_title, urls in CORPUS:
        if alias_of_title:
            by_title[title].alias_of = by_title[alias_of_title].tag_id
        for url in urls:
            db_session.add(TagExternalLinks(tag_id=by_title[title].tag_id, url=url))
    await db_session.commit()
    return by_title


def titles_for(by_title: dict[str, Tags], result) -> list[str]:
    id_to_title = {tag.tag_id: title for title, tag in by_title.items()}
    return [id_to_title[tag_id] for tag_id in result.tag_ids]


@pytest.mark.integration
class TestSearchCorpus:
    @pytest.mark.parametrize(("query", "expected"), EXPECTED_TOP1)
    async def test_top_hit(self, db_session: AsyncSession, query: str, expected: str):
        by_title = await seed_corpus(db_session)
        result = await search_tags(db_session, query, limit=10)
        titles = titles_for(by_title, result)
        assert titles, f"{query!r} returned nothing"
        assert titles[0] == expected, f"{query!r} -> {titles}"

    async def test_numeric_query_matches_digits_literally_only(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(by_title, await search_tags(db_session, "21412050"))
        assert "TKennshou" in found  # via its external URL
        off_by_one = await search_tags(db_session, "21412051")
        assert off_by_one.tag_ids == []
        assert off_by_one.total == 0

    async def test_description_only_match_is_found(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(by_title, await search_tags(db_session, "magical girl"))
        assert "Mahou no Stage Fancy Lala" in found

    async def test_alias_rows_rank_by_parent_count(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        # "neko" is an alias of "cat" (18,416); "Neko" the character has 10.
        found = titles_for(by_title, await search_tags(db_session, "neko"))
        assert found[:2] == ["neko", "Neko"]

    async def test_type_filter_and_exclude_aliases(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        by_type = {tag.tag_id: tag.type for tag in by_title.values()}
        only_characters = await search_tags(db_session, "sakura", type_filter=TagType.CHARACTER)
        assert only_characters.tag_ids
        assert all(by_type[tag_id] == TagType.CHARACTER for tag_id in only_characters.tag_ids)
        assert only_characters.total == len(only_characters.tag_ids)
        no_aliases_result = await search_tags(db_session, "sakura", exclude_aliases=True)
        no_aliases = titles_for(by_title, no_aliases_result)
        assert "sakura" not in no_aliases  # the alias row
        assert "Sakura" in no_aliases
        assert no_aliases_result.total == len(no_aliases_result.tag_ids)

    async def test_total_is_exact_and_pagination_is_stable(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        first = await search_tags(db_session, "sakura", limit=3, offset=0)
        second = await search_tags(db_session, "sakura", limit=3, offset=3)
        assert first.total == second.total
        assert not set(first.tag_ids) & set(second.tag_ids)
        everything = await search_tags(db_session, "sakura", limit=100)
        assert everything.total == len(everything.tag_ids)

    async def test_explicit_sort_overrides_relevance(self, db_session: AsyncSession):
        by_title = await seed_corpus(db_session)
        found = titles_for(
            by_title, await search_tags(db_session, "sakura", sort=["title:asc"], limit=100)
        )
        # Relevance would lead with the exact "sakura"; a title sort leads with "Kinomoto Sakura".
        assert found[0] == "Kinomoto Sakura"
        assert "sakura" in found

    async def test_empty_query_lists_all_by_effective_usage(self, db_session: AsyncSession):
        await seed_corpus(db_session)
        result = await search_tags(db_session, "", limit=2)
        assert result.total >= len(CORPUS)
        assert len(result.tag_ids) == 2

    async def test_expanding_long_query_does_not_error(self, db_session: AsyncSession):
        # 128 sharp-s characters fold to 256 characters; levenshtein caps at 255.
        # The corpus alone never puts a row into the scored (tier-4) CTE for a
        # query this long, so the crash would go untested without a decoy: seed
        # one whose description literally contains the string, which admits it
        # as a candidate but not a literal title match, forcing the levenshtein
        # comparison that overflows.
        await seed_corpus(db_session)
        decoy = Tags(title="Overflow Decoy", type=TagType.THEME, desc="ß" * 128)
        db_session.add(decoy)
        await db_session.commit()
        await db_session.refresh(decoy)
        result = await search_tags(db_session, "ß" * 128, limit=10)
        assert result.tag_ids == [decoy.tag_id]
        assert result.total == 1

"""Tests for the search API endpoint (/api/v1/search), Postgres engine."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from app.models.tag_external_link import TagExternalLinks


async def _seed(db_session: AsyncSession, *tags: Tags) -> list[Tags]:
    db_session.add_all(tags)
    await db_session.commit()
    for tag in tags:
        await db_session.refresh(tag)
    return list(tags)


async def _seed_identity_owner(
    db_session: AsyncSession, *, title: str = "TKennshou", alias_titles: tuple[str, ...] = ()
) -> tuple[Tags, list[Tags]]:
    """An artist owning pixiv id 21412050, plus optional alias rows pointing at it."""
    (owner,) = await _seed(db_session, Tags(title=title, type=TagType.ARTIST, usage_count=1))
    db_session.add(
        TagExternalLinks(
            tag_id=owner.tag_id,
            url="https://www.pixiv.net/users/21412050",
            site="pixiv",
            external_id="21412050",
        )
    )
    aliases = [Tags(title=t, type=TagType.ARTIST, alias_of=owner.tag_id) for t in alias_titles]
    db_session.add_all(aliases)
    await db_session.commit()
    for alias in aliases:
        await db_session.refresh(alias)
    return owner, aliases


@pytest.mark.api
class TestSearchEndpoint:
    async def test_search_returns_matching_tags_exact_first(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await _seed(
            db_session,
            Tags(
                title="Sakura Kinomoto", desc="Card Captor", type=TagType.CHARACTER, usage_count=50
            ),
            Tags(title="Sakura", desc="Cherry blossom", type=TagType.CHARACTER, usage_count=5),
        )
        response = await client.get("/api/v1/search", params={"q": "sakura"})
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "sakura"
        assert data["entity"] == "tags"
        assert data["total"] == 2
        assert data["limit"] == 20
        assert data["offset"] == 0
        assert [hit["title"] for hit in data["hits"]] == ["Sakura", "Sakura Kinomoto"]

    @pytest.mark.parametrize("params", [{}, {"q": ""}])
    async def test_missing_or_empty_query_lists_all(
        self, client: AsyncClient, db_session: AsyncSession, params
    ):
        await _seed(
            db_session,
            Tags(title="popular", type=TagType.THEME, usage_count=100),
            Tags(title="rare", type=TagType.THEME, usage_count=1),
        )
        response = await client.get("/api/v1/search", params=params)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert [hit["title"] for hit in data["hits"]] == ["popular", "rare"]

    async def test_search_with_type_filter(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(
            db_session,
            Tags(title="Naruto", type=TagType.SOURCE),
            Tags(title="Naruto Uzumaki", type=TagType.CHARACTER),
        )
        response = await client.get(
            "/api/v1/search", params={"q": "naruto", "type": TagType.SOURCE}
        )
        assert response.status_code == 200
        assert [hit["title"] for hit in response.json()["hits"]] == ["Naruto"]

    async def test_search_with_aliases_hide(self, client: AsyncClient, db_session: AsyncSession):
        (canonical,) = await _seed(db_session, Tags(title="test canonical", type=TagType.THEME))
        await _seed(
            db_session, Tags(title="test alias", type=TagType.THEME, alias_of=canonical.tag_id)
        )
        response = await client.get("/api/v1/search", params={"q": "test", "aliases": "hide"})
        assert response.status_code == 200
        assert [hit["title"] for hit in response.json()["hits"]] == ["test canonical"]

    async def test_search_no_results(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "nonexistent"})
        assert response.status_code == 200
        assert response.json() == {
            "query": "nonexistent",
            "entity": "tags",
            "hits": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
        }

    async def test_search_with_limit_and_offset(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await _seed(
            db_session,
            *[
                Tags(title=f"test {i:02d}", type=TagType.THEME, usage_count=100 - i)
                for i in range(8)
            ],
        )
        response = await client.get("/api/v1/search", params={"q": "test", "limit": 3, "offset": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 8
        assert data["limit"] == 3
        assert data["offset"] == 5
        assert [hit["title"] for hit in data["hits"]] == ["test 05", "test 06", "test 07"]

    async def test_search_rejects_offset_over_max(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "test", "offset": 500_001})
        assert response.status_code == 422

    async def test_search_honours_sort(self, client: AsyncClient, db_session: AsyncSession):
        await _seed(
            db_session,
            Tags(title="sakura b", type=TagType.THEME, usage_count=1),
            Tags(title="sakura a", type=TagType.THEME, usage_count=100),
        )
        response = await client.get(
            "/api/v1/search", params={"q": "sakura", "sort_by": "title", "sort_order": "ASC"}
        )
        assert [hit["title"] for hit in response.json()["hits"]] == ["sakura a", "sakura b"]

    async def test_search_rejects_invalid_sort_by(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/search", params={"q": "sakura", "sort_by": "not_a_field"}
        )
        assert response.status_code == 422

    async def test_search_populates_alias_of_name_for_alias_hits(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        (canonical,) = await _seed(
            db_session, Tags(title="feline", type=TagType.THEME, usage_count=42)
        )
        await _seed(db_session, Tags(title="cat", type=TagType.THEME, alias_of=canonical.tag_id))
        response = await client.get("/api/v1/search", params={"q": "cat"})
        hit = response.json()["hits"][0]
        assert hit["title"] == "cat"
        assert hit["is_alias"] is True
        assert hit["alias_of_name"] == "feline"
        assert hit["alias_of_usage_count"] == 42


@pytest.mark.api
class TestExactIdentityLayer:
    async def test_bare_id_prepends_owner_with_matched_identity(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050"})
        assert response.status_code == 200
        data = response.json()
        assert data["hits"][0]["tag_id"] == owner.tag_id
        assert data["hits"][0]["matched_identity"] == "Pixiv 21412050"
        # The owner is also a text hit through its URL, so the total is not inflated.
        assert data["total"] == 1
        assert [hit["tag_id"] for hit in data["hits"]].count(owner.tag_id) == 1

    async def test_text_query_has_no_matched_identity(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await _seed_identity_owner(db_session, title="Tsunekichi")
        response = await client.get("/api/v1/search", params={"q": "tsunekichi"})
        assert response.json()["hits"][0]["matched_identity"] is None

    async def test_alias_rows_of_the_owner_are_dropped(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, aliases = await _seed_identity_owner(db_session, alias_titles=("Pixiv 21412050",))
        response = await client.get("/api/v1/search", params={"q": "21412050"})
        data = response.json()
        ids = [hit["tag_id"] for hit in data["hits"]]
        assert ids == [owner.tag_id]
        assert data["total"] == 1

    async def test_prepend_respects_limit(self, client: AsyncClient, db_session: AsyncSession):
        owner, _ = await _seed_identity_owner(db_session)
        await _seed(
            db_session, Tags(title="21412050 fan club", type=TagType.THEME, usage_count=999)
        )
        response = await client.get("/api/v1/search", params={"q": "21412050", "limit": 1})
        data = response.json()
        assert [hit["tag_id"] for hit in data["hits"]] == [owner.tag_id]
        assert data["hits"][0]["matched_identity"] == "Pixiv 21412050"

    async def test_later_page_does_not_inject_and_total_is_consistent(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _ = await _seed_identity_owner(db_session)
        await _seed(
            db_session, Tags(title="21412050 fan club", type=TagType.THEME, usage_count=999)
        )
        first = (
            await client.get("/api/v1/search", params={"q": "21412050", "limit": 1, "offset": 0})
        ).json()
        second = (
            await client.get("/api/v1/search", params={"q": "21412050", "limit": 1, "offset": 1})
        ).json()
        # The identity layer resolves "already found" against page 1 for both requests,
        # so the totals agree; their exact value is the engine's count plus the
        # injected owner when it sits beyond page 1.
        assert first["total"] == second["total"]
        assert all(hit["matched_identity"] is None for hit in second["hits"])

    async def test_identity_query_drops_alias_row_on_later_page(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Ranking for "21412050": the prefix-titled theme first, then the alias row
        # (title contains the id), then the owner (matched only through its URL).
        # With limit=1 the alias row is page 2's only hit and must be dropped there too.
        owner, aliases = await _seed_identity_owner(db_session, alias_titles=("Pixiv 21412050",))
        await _seed(
            db_session, Tags(title="21412050 fan club", type=TagType.THEME, usage_count=999)
        )
        response = await client.get(
            "/api/v1/search", params={"q": "21412050", "limit": 1, "offset": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert all(hit["tag_id"] != aliases[0].tag_id for hit in data["hits"])
        assert all(hit["alias_of"] != owner.tag_id for hit in data["hits"])
        assert all(hit["matched_identity"] is None for hit in data["hits"])

    async def test_mismatched_type_filter_suppresses_injection(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await _seed_identity_owner(db_session)
        response = await client.get(
            "/api/v1/search", params={"q": "21412050", "type": TagType.THEME}
        )
        assert response.json()["hits"] == []

    async def test_matching_type_filter_keeps_injection(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get(
            "/api/v1/search", params={"q": "21412050", "type": TagType.ARTIST}
        )
        assert response.json()["hits"][0]["tag_id"] == owner.tag_id

    async def test_aliases_hide_blocks_an_alias_owner(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # An owner that is itself an alias must not be injected when aliases are excluded.
        (canonical,) = await _seed(db_session, Tags(title="Canonical Artist", type=TagType.ARTIST))
        (alias_owner,) = await _seed(
            db_session, Tags(title="Legacy", type=TagType.ARTIST, alias_of=canonical.tag_id)
        )
        db_session.add(
            TagExternalLinks(
                tag_id=alias_owner.tag_id,
                url="https://www.pixiv.net/users/21412050",
                site="pixiv",
                external_id="21412050",
            )
        )
        await db_session.commit()
        response = await client.get("/api/v1/search", params={"q": "21412050", "aliases": "hide"})
        assert all(hit["tag_id"] != alias_owner.tag_id for hit in response.json()["hits"])

    async def test_aliases_only_blocks_a_canonical_owner(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _ = await _seed_identity_owner(db_session, alias_titles=("Pixiv 21412050",))
        response = await client.get("/api/v1/search", params={"q": "21412050", "aliases": "only"})
        data = response.json()
        assert all(hit["matched_identity"] is None for hit in data["hits"])
        assert all(hit["tag_id"] != owner.tag_id for hit in data["hits"])


@pytest.mark.api
class TestTagListFilters:
    async def _seed_family(self, db_session: AsyncSession) -> dict[str, Tags]:
        (canonical,) = await _seed(
            db_session, Tags(title="feline", type=TagType.THEME, usage_count=500)
        )
        (alias,) = await _seed(
            db_session, Tags(title="feline alias", type=TagType.THEME, alias_of=canonical.tag_id)
        )
        (parent,) = await _seed(
            db_session, Tags(title="feline parent", type=TagType.THEME, usage_count=40)
        )
        (child,) = await _seed(
            db_session,
            Tags(
                title="feline child",
                type=TagType.THEME,
                usage_count=3,
                inheritedfrom_id=parent.tag_id,
            ),
        )
        (character,) = await _seed(
            db_session, Tags(title="feline girl", type=TagType.CHARACTER, usage_count=20)
        )
        (loner,) = await _seed(
            db_session, Tags(title="feline loner", type=TagType.CHARACTER, usage_count=7)
        )
        (source,) = await _seed(
            db_session, Tags(title="feline show", type=TagType.SOURCE, usage_count=90)
        )
        db_session.add(
            CharacterSourceLinks(character_tag_id=character.tag_id, source_tag_id=source.tag_id)
        )
        await db_session.commit()
        return {
            "canonical": canonical,
            "alias": alias,
            "parent": parent,
            "child": child,
            "character": character,
            "loner": loner,
            "source": source,
        }

    async def _titles(self, client: AsyncClient, **params) -> tuple[list[str], int]:
        response = await client.get("/api/v1/search", params={"q": "feline", **params})
        assert response.status_code == 200, response.text
        data = response.json()
        return [hit["title"] for hit in data["hits"]], data["total"]

    async def test_aliases_only_and_all(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        only, only_total = await self._titles(client, aliases="only")
        assert only == ["feline alias"] and only_total == 1
        every, every_total = await self._titles(client, aliases="all")
        assert "feline alias" in every and every_total == 7

    async def test_min_usage_counts_the_alias_by_its_parent(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await self._seed_family(db_session)
        titles, total = await self._titles(client, aliases="all", min_usage=100)
        assert set(titles) == {"feline", "feline alias"} and total == 2

    async def test_max_usage_bounds_the_effective_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await self._seed_family(db_session)
        titles, total = await self._titles(client, aliases="all", min_usage=100, max_usage=600)
        assert set(titles) == {"feline", "feline alias"} and total == 2
        titles, total = await self._titles(client, max_usage=10)
        assert set(titles) == {"feline child", "feline loner"} and total == 2

    async def test_min_usage_above_max_usage_is_422(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/search", params={"q": "", "min_usage": 5, "max_usage": 4}
        )
        assert response.status_code == 422

    async def test_added_range(self, client: AsyncClient, db_session: AsyncSession):
        family = await self._seed_family(db_session)
        family["loner"].date_added = datetime(2020, 6, 15, 12, 0, tzinfo=UTC)
        await db_session.commit()
        titles, total = await self._titles(client, added_from="2020-01-01", added_to="2020-12-31")
        assert titles == ["feline loner"] and total == 1
        titles, total = await self._titles(client, added_to="2019-12-31")
        assert titles == [] and total == 0

    async def test_added_from_after_added_to_is_422(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/search", params={"q": "", "added_from": "2021-01-01", "added_to": "2020-01-01"}
        )
        assert response.status_code == 422

    async def test_has_alias_is_child_has_children(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await self._seed_family(db_session)
        assert (await self._titles(client, has_alias="yes"))[0] == ["feline"]
        assert (await self._titles(client, is_child="yes"))[0] == ["feline child"]
        assert (await self._titles(client, has_children="yes"))[0] == ["feline parent"]
        titles, total = await self._titles(client, has_children="no", is_child="no", has_alias="no")
        assert set(titles) == {"feline alias", "feline girl", "feline loner", "feline show"}
        assert total == 4

    async def test_source_linked_by_type(self, client: AsyncClient, db_session: AsyncSession):
        await self._seed_family(db_session)
        assert (await self._titles(client, type=TagType.CHARACTER, source_linked="yes"))[0] == [
            "feline girl"
        ]
        assert (await self._titles(client, type=TagType.CHARACTER, source_linked="no"))[0] == [
            "feline loner"
        ]
        assert (await self._titles(client, type=TagType.SOURCE, source_linked="yes"))[0] == [
            "feline show"
        ]

    @pytest.mark.parametrize(
        "params", [{}, {"type": 0}, {"type": TagType.THEME}, {"type": TagType.ARTIST}]
    )
    async def test_source_linked_needs_character_or_source_type(self, client: AsyncClient, params):
        response = await client.get(
            "/api/v1/search", params={"q": "", "source_linked": "yes", **params}
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "params",
        [
            {"aliases": "sometimes"},
            {"min_usage": -1},
            {"min_usage": 2147483648},
            {"max_usage": -1},
            {"max_usage": 2147483648},
            {"has_alias": "maybe"},
            {"added_from": "2020-13-01"},
        ],
    )
    async def test_invalid_values_are_422(self, client: AsyncClient, params):
        response = await client.get("/api/v1/search", params={"q": "", **params})
        assert response.status_code == 422

    async def test_min_usage_at_the_integer_maximum_is_accepted(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "", "min_usage": 2147483647})
        assert response.status_code == 200

    async def test_identity_prepend_skips_under_a_structural_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _ = await _seed_identity_owner(db_session)
        response = await client.get("/api/v1/search", params={"q": "21412050", "min_usage": 0})
        data = response.json()
        assert all(hit["matched_identity"] is None for hit in data["hits"])
        assert owner.tag_id in [
            hit["tag_id"] for hit in data["hits"]
        ]  # still a plain text hit via its URL

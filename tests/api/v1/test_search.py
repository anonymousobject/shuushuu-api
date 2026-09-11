"""Tests for the search API endpoint (/api/v1/search), Postgres engine."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
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

    async def test_search_with_exclude_aliases(self, client: AsyncClient, db_session: AsyncSession):
        (canonical,) = await _seed(db_session, Tags(title="test canonical", type=TagType.THEME))
        await _seed(
            db_session, Tags(title="test alias", type=TagType.THEME, alias_of=canonical.tag_id)
        )
        response = await client.get("/api/v1/search", params={"q": "test", "exclude_aliases": True})
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

    async def test_exclude_aliases_blocks_an_alias_owner(
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
        response = await client.get(
            "/api/v1/search", params={"q": "21412050", "exclude_aliases": True}
        )
        assert all(hit["tag_id"] != alias_owner.tag_id for hit in response.json()["hits"])

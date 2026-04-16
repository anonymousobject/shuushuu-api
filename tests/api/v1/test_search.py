"""Tests for the search API endpoint (/api/v1/search)."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.search import get_search_service
from app.config import TagType
from app.models.tag import Tags
from app.services.search import TagSearchResult


@pytest.fixture
def mock_search_service():
    """Create a mock SearchService with async search_tags method."""
    service = AsyncMock()
    service.search_tags.return_value = TagSearchResult(tag_ids=[], total=0)
    return service


@pytest.fixture
def search_client(app: FastAPI, mock_search_service):
    """Override the search service dependency on the test app."""
    app.dependency_overrides[get_search_service] = lambda: mock_search_service
    return app


@pytest.fixture
async def client_with_search(search_client: FastAPI):
    """AsyncClient wired to the app with search service override."""
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=search_client),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.api
class TestSearchEndpoint:
    """Tests for GET /api/v1/search."""

    async def test_search_returns_matching_tags(
        self,
        client_with_search: AsyncClient,
        db_session: AsyncSession,
        mock_search_service: AsyncMock,
    ):
        """Search returns full tag data for IDs returned by Meilisearch."""
        # Create tags in DB
        tag1 = Tags(title="Sakura", desc="Cherry blossom", type=TagType.CHARACTER)
        tag2 = Tags(title="Sakura Kinomoto", desc="Card Captor", type=TagType.CHARACTER)
        db_session.add_all([tag1, tag2])
        await db_session.commit()
        await db_session.refresh(tag1)
        await db_session.refresh(tag2)

        # Mock Meilisearch returning both tag IDs
        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[tag1.tag_id, tag2.tag_id], total=2
        )

        response = await client_with_search.get("/api/v1/search", params={"q": "sakura"})
        assert response.status_code == 200

        data = response.json()
        assert data["query"] == "sakura"
        assert data["entity"] == "tags"
        assert data["total"] == 2
        assert data["limit"] == 20
        assert data["offset"] == 0
        assert len(data["hits"]) == 2
        assert data["hits"][0]["title"] == "Sakura"
        assert data["hits"][1]["title"] == "Sakura Kinomoto"

    async def test_search_missing_query_returns_422(
        self,
        client_with_search: AsyncClient,
    ):
        """Calling search without the required 'q' parameter returns 422."""
        response = await client_with_search.get("/api/v1/search")
        assert response.status_code == 422

    async def test_search_empty_query_returns_422(
        self,
        client_with_search: AsyncClient,
    ):
        """An empty 'q' parameter (min_length=1) returns 422."""
        response = await client_with_search.get("/api/v1/search", params={"q": ""})
        assert response.status_code == 422

    async def test_search_with_type_filter(
        self,
        client_with_search: AsyncClient,
        db_session: AsyncSession,
        mock_search_service: AsyncMock,
    ):
        """The type filter is forwarded to search_tags."""
        tag = Tags(title="Naruto", desc="Anime source", type=TagType.SOURCE)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[tag.tag_id], total=1
        )

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "naruto", "type": TagType.SOURCE}
        )
        assert response.status_code == 200

        # Verify the type filter was passed through to the service
        mock_search_service.search_tags.assert_called_once_with(
            "naruto",
            limit=20,
            offset=0,
            type_filter=TagType.SOURCE,
            exclude_aliases=False,
        )

    async def test_search_with_exclude_aliases(
        self,
        client_with_search: AsyncClient,
        mock_search_service: AsyncMock,
    ):
        """The exclude_aliases flag is forwarded to search_tags."""
        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[], total=0
        )

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "test", "exclude_aliases": True}
        )
        assert response.status_code == 200

        mock_search_service.search_tags.assert_called_once_with(
            "test",
            limit=20,
            offset=0,
            type_filter=None,
            exclude_aliases=True,
        )

    async def test_search_preserves_meilisearch_order(
        self,
        client_with_search: AsyncClient,
        db_session: AsyncSession,
        mock_search_service: AsyncMock,
    ):
        """Response preserves Meilisearch relevance order, not DB ID order."""
        # Create tags with ascending IDs
        tag_a = Tags(title="Alpha", type=TagType.THEME)
        tag_b = Tags(title="Beta", type=TagType.THEME)
        tag_c = Tags(title="Gamma", type=TagType.THEME)
        db_session.add_all([tag_a, tag_b, tag_c])
        await db_session.commit()
        await db_session.refresh(tag_a)
        await db_session.refresh(tag_b)
        await db_session.refresh(tag_c)

        # Meilisearch returns them in reverse order (by relevance)
        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[tag_c.tag_id, tag_a.tag_id, tag_b.tag_id], total=3
        )

        response = await client_with_search.get("/api/v1/search", params={"q": "test"})
        assert response.status_code == 200

        data = response.json()
        titles = [hit["title"] for hit in data["hits"]]
        assert titles == ["Gamma", "Alpha", "Beta"]

    async def test_search_no_results(
        self,
        client_with_search: AsyncClient,
        mock_search_service: AsyncMock,
    ):
        """Empty Meilisearch results yield empty hits list."""
        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[], total=0
        )

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "nonexistent"}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["hits"] == []
        assert data["total"] == 0
        assert data["query"] == "nonexistent"

    async def test_search_with_limit_and_offset(
        self,
        client_with_search: AsyncClient,
        db_session: AsyncSession,
        mock_search_service: AsyncMock,
    ):
        """Custom limit and offset are forwarded and reflected in response."""
        tag = Tags(title="Test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[tag.tag_id], total=50
        )

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "test", "limit": 10, "offset": 5}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 5
        assert data["total"] == 50

        mock_search_service.search_tags.assert_called_once_with(
            "test",
            limit=10,
            offset=5,
            type_filter=None,
            exclude_aliases=False,
        )

    async def test_search_returns_503_when_meilisearch_unavailable(
        self,
        app: FastAPI,
    ):
        """Without Meilisearch, the search endpoint returns 503."""
        from httpx import ASGITransport

        # No dependency override — default get_search_service raises HTTPException(503)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/api/v1/search", params={"q": "test"})
        assert response.status_code == 503
        assert response.json()["detail"] == "Search service is not available"

    async def test_search_returns_503_when_meilisearch_fails_mid_request(
        self,
        client_with_search: AsyncClient,
        mock_search_service: AsyncMock,
    ):
        """If Meilisearch errors during search, endpoint returns 503."""
        mock_search_service.search_tags.side_effect = Exception("Connection refused")

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "test"}
        )
        assert response.status_code == 503
        assert "temporarily unavailable" in response.json()["detail"]

    async def test_search_rejects_offset_over_1000(
        self,
        client_with_search: AsyncClient,
        mock_search_service: AsyncMock,
    ):
        """Offset above Meilisearch's maxTotalHits returns 422."""
        response = await client_with_search.get(
            "/api/v1/search", params={"q": "test", "offset": 1001}
        )
        assert response.status_code == 422

    async def test_search_handles_missing_db_tags_gracefully(
        self,
        client_with_search: AsyncClient,
        mock_search_service: AsyncMock,
    ):
        """If Meilisearch returns IDs that don't exist in DB, they're skipped."""
        mock_search_service.search_tags.return_value = TagSearchResult(
            tag_ids=[99999, 99998], total=2
        )

        response = await client_with_search.get(
            "/api/v1/search", params={"q": "ghost"}
        )
        assert response.status_code == 200

        data = response.json()
        # Total comes from Meilisearch, but hits only include DB-verified tags
        assert data["total"] == 2
        assert data["hits"] == []

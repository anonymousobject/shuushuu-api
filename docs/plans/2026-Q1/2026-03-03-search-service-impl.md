# Search Service Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Meilisearch-backed search service with a new `/api/v1/search` endpoint, running alongside the existing MySQL fulltext search for comparison.

**Architecture:** A `SearchService` class wraps the Meilisearch async client, providing tag indexing and search methods. It's initialized during app lifespan and injected via FastAPI dependency. Tag write paths push updates to Meilisearch after MySQL commits. A new `/api/v1/search` endpoint exposes Meilisearch-powered search while existing endpoints remain untouched.

**Tech Stack:** `meilisearch-python-sdk` (async client), Meilisearch v1 (Docker), FastAPI dependency injection

**Design doc:** `docs/plans/2026-Q1/2026-03-03-search-service-design.md`

---

### Task 1: Add Meilisearch Infrastructure

**Files:**
- Modify: `pyproject.toml:12-42` (dependencies)
- Modify: `app/config.py:48-50` (after Redis settings)
- Modify: `docker-compose.yml:56-57` (after redis service)

**Step 1: Add dependency**

In `pyproject.toml`, add to the `dependencies` list after the redis entry (line 21):

```toml
    "meilisearch-python-sdk>=3.0.0",
```

**Step 2: Add configuration settings**

In `app/config.py`, add after the Redis settings block (after line 50):

```python
    # Meilisearch
    MEILISEARCH_URL: str = Field(default="http://localhost:7700")
    MEILISEARCH_API_KEY: str | None = Field(default=None)
```

**Step 3: Add Docker Compose service**

In `docker-compose.yml`, add after the redis service (after line 56):

```yaml
  # Meilisearch (search engine)
  meilisearch:
    image: getmeili/meilisearch:v1
    container_name: shuushuu-meilisearch
    environment:
      - MEILI_MASTER_KEY=${MEILI_MASTER_KEY:-dev_master_key}
      - MEILI_ENV=${ENVIRONMENT:-development}
    ports:
      - "7700:7700"
    volumes:
      - meilisearch_data:/meili_data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7700/health"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
```

Add `meilisearch_data` to the `volumes` section at the bottom:

```yaml
  meilisearch_data:
    driver: local
```

**Step 4: Install dependency**

Run: `uv sync`

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock app/config.py docker-compose.yml
git commit -m "feat: add Meilisearch infrastructure (dependency, config, Docker)"
```

---

### Task 2: Create Meilisearch Client Module

**Files:**
- Create: `app/core/meilisearch.py`
- Test: `tests/unit/test_meilisearch_client.py`

**Step 1: Write the failing test**

Create `tests/unit/test_meilisearch_client.py`:

```python
"""Tests for Meilisearch client dependency."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.meilisearch import get_meilisearch


@pytest.mark.unit
class TestGetMeilisearch:
    """Tests for get_meilisearch dependency."""

    async def test_yields_async_client(self):
        """get_meilisearch yields an AsyncClient instance."""
        with patch("app.core.meilisearch.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            gen = get_meilisearch()
            client = await gen.__anext__()

            assert client is mock_client

            # Cleanup
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

    async def test_closes_client_on_cleanup(self):
        """Client is closed when the dependency is cleaned up."""
        with patch("app.core.meilisearch.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            gen = get_meilisearch()
            await gen.__anext__()

            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

            mock_client.aclose.assert_awaited_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_meilisearch_client.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

Create `app/core/meilisearch.py`:

```python
from collections.abc import AsyncGenerator

from meilisearch_python_sdk import AsyncClient

from app.config import settings


async def get_meilisearch() -> AsyncGenerator[AsyncClient]:
    """Dependency for getting async Meilisearch client."""
    client = AsyncClient(
        url=settings.MEILISEARCH_URL,
        api_key=settings.MEILISEARCH_API_KEY,
    )
    try:
        yield client
    finally:
        await client.aclose()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_meilisearch_client.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/core/meilisearch.py tests/unit/test_meilisearch_client.py
git commit -m "feat: add Meilisearch client dependency module"
```

---

### Task 3: Create Search Schemas

**Files:**
- Create: `app/schemas/search.py`

**Step 1: Create the schema file**

Create `app/schemas/search.py`:

```python
"""Pydantic schemas for the search endpoint."""

from pydantic import BaseModel, Field

from app.schemas.tag import TagResponse


class SearchRequest(BaseModel):
    """Query parameters for the search endpoint."""

    q: str = Field(min_length=1, max_length=200, description="Search query")
    entity: str = Field(default="tags", pattern="^(tags)$", description="Entity type to search")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results to return")
    offset: int = Field(default=0, ge=0, description="Number of results to skip")


class TagSearchHit(TagResponse):
    """A tag search result from Meilisearch, extending the standard tag response."""

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    """Response from the search endpoint."""

    query: str
    entity: str
    hits: list[TagSearchHit]
    total: int
    limit: int
    offset: int
```

No tests needed for pure schema definitions — they'll be exercised by service and API tests.

**Step 2: Commit**

```bash
git add app/schemas/search.py
git commit -m "feat: add search request/response schemas"
```

---

### Task 4: Create Search Service — Tag Indexing

**Files:**
- Create: `app/services/search.py`
- Create: `tests/unit/test_search_service.py`

**Step 1: Write the failing tests for tag indexing**

Create `tests/unit/test_search_service.py`:

```python
"""Unit tests for the search service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import TagType
from app.models.tag import Tags
from app.services.search import SearchService

TAGS_INDEX_NAME = "tags"


def _make_mock_client() -> AsyncMock:
    """Create a mock Meilisearch client with an index mock."""
    client = AsyncMock()
    index_mock = AsyncMock()
    client.index.return_value = index_mock
    return client


def _make_tag(**overrides) -> Tags:
    """Create a Tags instance with defaults."""
    defaults = {
        "tag_id": 1,
        "title": "Sakura Kinomoto",
        "desc": "Main character from Cardcaptor Sakura",
        "type": TagType.CHARACTER,
        "usage_count": 42,
        "alias_of": None,
    }
    defaults.update(overrides)
    return Tags(**defaults)


@pytest.mark.unit
class TestIndexTag:
    """Tests for SearchService.index_tag."""

    async def test_sends_correct_document_shape(self):
        """index_tag sends a document with the expected fields."""
        client = _make_mock_client()
        service = SearchService(client)
        tag = _make_tag()

        await service.index_tag(tag)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.add_documents.assert_awaited_once()
        docs = index_mock.add_documents.call_args[0][0]
        assert len(docs) == 1
        assert docs[0] == {
            "tag_id": 1,
            "title": "Sakura Kinomoto",
            "desc": "Main character from Cardcaptor Sakura",
            "type": TagType.CHARACTER,
            "usage_count": 42,
            "alias_of": None,
        }

    async def test_index_tag_with_alias(self):
        """index_tag includes alias_of when set."""
        client = _make_mock_client()
        service = SearchService(client)
        tag = _make_tag(alias_of=99)

        await service.index_tag(tag)

        index_mock = client.index(TAGS_INDEX_NAME)
        docs = index_mock.add_documents.call_args[0][0]
        assert docs[0]["alias_of"] == 99


@pytest.mark.unit
class TestIndexTags:
    """Tests for SearchService.index_tags (bulk)."""

    async def test_sends_multiple_documents(self):
        """index_tags sends all tags in one call."""
        client = _make_mock_client()
        service = SearchService(client)
        tags = [_make_tag(tag_id=i, title=f"Tag {i}") for i in range(3)]

        await service.index_tags(tags)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.add_documents.assert_awaited_once()
        docs = index_mock.add_documents.call_args[0][0]
        assert len(docs) == 3

    async def test_empty_list_does_nothing(self):
        """index_tags with empty list does not call Meilisearch."""
        client = _make_mock_client()
        service = SearchService(client)

        await service.index_tags([])

        client.index.assert_not_called()


@pytest.mark.unit
class TestDeleteTag:
    """Tests for SearchService.delete_tag."""

    async def test_deletes_by_tag_id(self):
        """delete_tag calls delete_document with the tag_id."""
        client = _make_mock_client()
        service = SearchService(client)

        await service.delete_tag(42)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.delete_document.assert_awaited_once_with("42")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_search_service.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

Create `app/services/search.py`:

```python
"""Search service for Meilisearch integration."""

from meilisearch_python_sdk import AsyncClient

from app.core.logging import get_logger
from app.models.tag import Tags

logger = get_logger(__name__)

TAGS_INDEX_NAME = "tags"


def _tag_to_document(tag: Tags) -> dict:
    """Convert a Tags model to a Meilisearch document."""
    return {
        "tag_id": tag.tag_id,
        "title": tag.title,
        "desc": tag.desc,
        "type": tag.type,
        "usage_count": tag.usage_count,
        "alias_of": tag.alias_of,
    }


class SearchService:
    """Service for indexing and searching via Meilisearch."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def index_tag(self, tag: Tags) -> None:
        """Index or update a single tag in Meilisearch."""
        doc = _tag_to_document(tag)
        index = self.client.index(TAGS_INDEX_NAME)
        await index.add_documents([doc])
        logger.debug("meilisearch_tag_indexed", tag_id=tag.tag_id)

    async def index_tags(self, tags: list[Tags]) -> None:
        """Bulk index multiple tags in Meilisearch."""
        if not tags:
            return
        docs = [_tag_to_document(tag) for tag in tags]
        index = self.client.index(TAGS_INDEX_NAME)
        await index.add_documents(docs)
        logger.debug("meilisearch_tags_indexed", count=len(docs))

    async def delete_tag(self, tag_id: int) -> None:
        """Remove a tag from the Meilisearch index."""
        index = self.client.index(TAGS_INDEX_NAME)
        await index.delete_document(str(tag_id))
        logger.debug("meilisearch_tag_deleted", tag_id=tag_id)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_search_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/search.py tests/unit/test_search_service.py
git commit -m "feat: add SearchService with tag indexing methods"
```

---

### Task 5: Add Search Method to Service

**Files:**
- Modify: `app/services/search.py`
- Modify: `tests/unit/test_search_service.py`

**Step 1: Write the failing tests for search_tags**

Append to `tests/unit/test_search_service.py`:

```python
@pytest.mark.unit
class TestSearchTags:
    """Tests for SearchService.search_tags."""

    async def test_returns_tag_ids_in_order(self):
        """search_tags returns tag IDs in Meilisearch relevance order."""
        client = _make_mock_client()
        service = SearchService(client)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.search.return_value = MagicMock(
            hits=[
                {"tag_id": 10, "title": "Sakura Kinomoto"},
                {"tag_id": 20, "title": "Sakura"},
            ],
            estimated_total_hits=2,
        )

        result = await service.search_tags("sakura")

        index_mock.search.assert_awaited_once()
        assert result.tag_ids == [10, 20]
        assert result.total == 2

    async def test_passes_limit_and_offset(self):
        """search_tags forwards limit and offset to Meilisearch."""
        client = _make_mock_client()
        service = SearchService(client)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.search.return_value = MagicMock(
            hits=[], estimated_total_hits=0,
        )

        await service.search_tags("test", limit=5, offset=10)

        call_kwargs = index_mock.search.call_args
        assert call_kwargs[1]["limit"] == 5
        assert call_kwargs[1]["offset"] == 10

    async def test_applies_type_filter(self):
        """search_tags passes type filter to Meilisearch."""
        client = _make_mock_client()
        service = SearchService(client)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.search.return_value = MagicMock(
            hits=[], estimated_total_hits=0,
        )

        await service.search_tags("test", type_filter=TagType.ARTIST)

        call_kwargs = index_mock.search.call_args
        assert "type = 3" in call_kwargs[1]["filter"]

    async def test_applies_exclude_aliases_filter(self):
        """search_tags can exclude alias tags."""
        client = _make_mock_client()
        service = SearchService(client)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.search.return_value = MagicMock(
            hits=[], estimated_total_hits=0,
        )

        await service.search_tags("test", exclude_aliases=True)

        call_kwargs = index_mock.search.call_args
        assert "alias_of IS NULL" in call_kwargs[1]["filter"]

    async def test_empty_query_returns_empty(self):
        """search_tags with empty results returns empty list."""
        client = _make_mock_client()
        service = SearchService(client)

        index_mock = client.index(TAGS_INDEX_NAME)
        index_mock.search.return_value = MagicMock(
            hits=[], estimated_total_hits=0,
        )

        result = await service.search_tags("nonexistent")

        assert result.tag_ids == []
        assert result.total == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_search_service.py::TestSearchTags -v`
Expected: FAIL (search_tags not defined)

**Step 3: Implement search_tags**

Add to `app/services/search.py`:

```python
from dataclasses import dataclass


@dataclass
class TagSearchResult:
    """Result from a tag search operation."""

    tag_ids: list[int]
    total: int
```

Add the `search_tags` method to `SearchService`:

```python
    async def search_tags(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        type_filter: int | None = None,
        exclude_aliases: bool = False,
    ) -> TagSearchResult:
        """Search tags via Meilisearch.

        Args:
            query: Search text
            limit: Max results to return
            offset: Number of results to skip
            type_filter: Filter by tag type (TagType constant)
            exclude_aliases: If True, exclude tags that are aliases

        Returns:
            TagSearchResult with ordered tag IDs and total count
        """
        filters: list[str] = []
        if type_filter is not None:
            filters.append(f"type = {type_filter}")
        if exclude_aliases:
            filters.append("alias_of IS NULL")

        filter_str = " AND ".join(filters) if filters else None

        index = self.client.index(TAGS_INDEX_NAME)
        results = await index.search(
            query,
            limit=limit,
            offset=offset,
            filter=filter_str,
        )

        tag_ids = [hit["tag_id"] for hit in results.hits]
        logger.debug(
            "meilisearch_tag_search",
            query=query,
            hits=len(tag_ids),
            total=results.estimated_total_hits,
        )
        return TagSearchResult(tag_ids=tag_ids, total=results.estimated_total_hits)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_search_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/search.py tests/unit/test_search_service.py
git commit -m "feat: add search_tags method to SearchService"
```

---

### Task 6: Create Search API Endpoint

**Files:**
- Create: `app/api/v1/search.py`
- Modify: `app/api/v1/__init__.py:7-22` (add import and router)
- Create: `tests/api/v1/test_search.py`

**Step 1: Write the failing test**

Create `tests/api/v1/test_search.py`:

```python
"""Tests for GET /api/v1/search endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagType
from app.models.tag import Tags


@pytest.mark.api
class TestSearchEndpoint:
    """Tests for GET /api/v1/search."""

    async def test_search_returns_matching_tags(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Search returns tags matching the query."""
        # Create test tags
        tags = [
            Tags(title="Sakura Kinomoto", type=TagType.CHARACTER, usage_count=100),
            Tags(title="Sakurajima Mai", type=TagType.CHARACTER, usage_count=50),
            Tags(title="Blue Sky", type=TagType.THEME, usage_count=10),
        ]
        for tag in tags:
            db_session.add(tag)
        await db_session.commit()
        for tag in tags:
            await db_session.refresh(tag)

        # Mock the search service to return matching tag IDs
        mock_search_result = MagicMock()
        mock_search_result.tag_ids = [tags[0].tag_id, tags[1].tag_id]
        mock_search_result.total = 2

        with patch("app.api.v1.search.get_search_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.search_tags.return_value = mock_search_result
            mock_get_service.return_value = mock_service

            response = await client.get("/api/v1/search", params={"q": "sakura"})

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "sakura"
        assert data["entity"] == "tags"
        assert data["total"] == 2
        assert len(data["hits"]) == 2
        assert data["hits"][0]["title"] == "Sakura Kinomoto"
        assert data["hits"][1]["title"] == "Sakurajima Mai"

    async def test_search_missing_query_returns_422(self, client: AsyncClient):
        """Search without q parameter returns validation error."""
        with patch("app.api.v1.search.get_search_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.get("/api/v1/search")

        assert response.status_code == 422

    async def test_search_with_type_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Search passes type filter to the service."""
        mock_search_result = MagicMock()
        mock_search_result.tag_ids = []
        mock_search_result.total = 0

        with patch("app.api.v1.search.get_search_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.search_tags.return_value = mock_search_result
            mock_get_service.return_value = mock_service

            response = await client.get(
                "/api/v1/search",
                params={"q": "test", "type": 3},
            )

        assert response.status_code == 200
        mock_service.search_tags.assert_awaited_once()
        call_kwargs = mock_service.search_tags.call_args[1]
        assert call_kwargs["type_filter"] == 3

    async def test_search_preserves_meilisearch_order(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Results are returned in Meilisearch's relevance order, not DB order."""
        # Create tags with IDs that would be in different DB order
        tag_a = Tags(title="Alpha", type=TagType.THEME, usage_count=10)
        tag_b = Tags(title="Beta", type=TagType.THEME, usage_count=100)
        db_session.add_all([tag_a, tag_b])
        await db_session.commit()
        await db_session.refresh(tag_a)
        await db_session.refresh(tag_b)

        # Meilisearch returns Beta first (higher relevance), despite lower DB ID
        mock_search_result = MagicMock()
        mock_search_result.tag_ids = [tag_b.tag_id, tag_a.tag_id]
        mock_search_result.total = 2

        with patch("app.api.v1.search.get_search_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.search_tags.return_value = mock_search_result
            mock_get_service.return_value = mock_service

            response = await client.get("/api/v1/search", params={"q": "test"})

        data = response.json()
        assert data["hits"][0]["title"] == "Beta"
        assert data["hits"][1]["title"] == "Alpha"

    async def test_search_no_results(self, client: AsyncClient):
        """Search with no matching results returns empty hits."""
        mock_search_result = MagicMock()
        mock_search_result.tag_ids = []
        mock_search_result.total = 0

        with patch("app.api.v1.search.get_search_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.search_tags.return_value = mock_search_result
            mock_get_service.return_value = mock_service

            response = await client.get("/api/v1/search", params={"q": "nonexistent"})

        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == []
        assert data["total"] == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/v1/test_search.py -v`
Expected: FAIL (module not found)

**Step 3: Implement the endpoint**

Create `app/api/v1/search.py`:

```python
"""Search endpoint powered by Meilisearch."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.tag import Tags
from app.schemas.search import SearchResponse, TagSearchHit
from app.services.search import SearchService

logger = get_logger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


def get_search_service() -> SearchService:
    """Get the search service instance.

    This is overridden at startup once Meilisearch is initialized.
    Raises RuntimeError if called before initialization.
    """
    raise RuntimeError("SearchService not initialized")


@router.get("", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(min_length=1, max_length=200, description="Search query")],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Annotated[int | None, Query(description="Filter by tag type")] = None,
    exclude_aliases: Annotated[bool, Query(description="Exclude alias tags")] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Results to skip")] = 0,
    search_service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search across entities using Meilisearch.

    Currently supports tag search. Returns results in relevance order.
    """
    result = await search_service.search_tags(
        q,
        limit=limit,
        offset=offset,
        type_filter=type,
        exclude_aliases=exclude_aliases,
    )

    # Fetch full tag records from MySQL, preserving Meilisearch order
    hits: list[TagSearchHit] = []
    if result.tag_ids:
        query = select(Tags).where(Tags.tag_id.in_(result.tag_ids))
        db_result = await db.execute(query)
        tags_by_id = {tag.tag_id: tag for tag in db_result.scalars().all()}

        for tag_id in result.tag_ids:
            tag = tags_by_id.get(tag_id)
            if tag:
                hits.append(TagSearchHit.model_validate(tag))

    return SearchResponse(
        query=q,
        entity="tags",
        hits=hits,
        total=result.total,
        limit=limit,
        offset=offset,
    )
```

**Step 4: Register the router**

In `app/api/v1/__init__.py`, add to imports (after line 20):

```python
    search,
```

Add to router includes (after the tags router, around line 33):

```python
router.include_router(search.router)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/v1/test_search.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add app/api/v1/search.py app/api/v1/__init__.py tests/api/v1/test_search.py
git commit -m "feat: add GET /api/v1/search endpoint"
```

---

### Task 7: Initialize SearchService in App Lifespan

**Files:**
- Modify: `app/main.py:76-93` (lifespan function)
- Modify: `app/api/v1/search.py` (override get_search_service)

**Step 1: Update lifespan to initialize Meilisearch**

In `app/main.py`, update the lifespan function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info(
        "application_starting",
        environment=settings.ENVIRONMENT,
        version="2.0.0",
    )

    # Sync permissions: ensure database matches Permission enum
    async with AsyncSessionLocal() as db:
        await sync_permissions(db)

    # Initialize Meilisearch search service
    from app.services.search import SearchService, configure_tags_index

    meilisearch_client = None
    try:
        from meilisearch_python_sdk import AsyncClient

        meilisearch_client = AsyncClient(
            url=settings.MEILISEARCH_URL,
            api_key=settings.MEILISEARCH_API_KEY,
        )
        search_service = SearchService(meilisearch_client)
        await configure_tags_index(meilisearch_client)

        # Override the search endpoint's dependency
        from app.api.v1 import search as search_module

        search_module.get_search_service = lambda: search_service
        logger.info("meilisearch_initialized", url=settings.MEILISEARCH_URL)
    except Exception:
        logger.warning("meilisearch_unavailable", url=settings.MEILISEARCH_URL)

    yield

    # Shutdown
    logger.info("application_shutting_down")
    if meilisearch_client:
        await meilisearch_client.aclose()
    await close_queue()  # Close arq pool
```

**Step 2: Add configure_tags_index to search service**

Add to `app/services/search.py`:

```python
async def configure_tags_index(client: AsyncClient) -> None:
    """Create and configure the tags index in Meilisearch.

    Sets ranking rules, filterable attributes, and searchable attributes.
    Idempotent — safe to call on every startup.
    """
    try:
        await client.create_index(TAGS_INDEX_NAME, primary_key="tag_id")
    except Exception:
        pass  # Index may already exist

    index = client.index(TAGS_INDEX_NAME)
    await index.update_ranking_rules([
        "words",
        "typo",
        "proximity",
        "attribute",
        "exactness",
        "usage_count:desc",
    ])
    await index.update_filterable_attributes(["type", "alias_of"])
    await index.update_searchable_attributes(["title", "desc"])
    await index.update_sortable_attributes(["usage_count"])

    logger.info("meilisearch_tags_index_configured")
```

**Step 3: Run existing tests to verify nothing is broken**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All existing tests PASS (Meilisearch init is wrapped in try/except)

**Step 4: Commit**

```bash
git add app/main.py app/services/search.py
git commit -m "feat: initialize Meilisearch SearchService in app lifespan"
```

---

### Task 8: Add Meilisearch Sync to Tag Write Paths

This task adds `search_service.index_tag()` calls after tag create/update/delete operations.
The sync is fire-and-forget — if Meilisearch is down, the MySQL write still succeeds.

**Files:**
- Modify: `app/api/v1/tags.py` (create_tag ~line 1120, update_tag ~line 1347, delete_tag ~line 1370)
- Modify: `app/api/v1/images.py` (add_tag_to_image ~line 1533, remove_tag_from_image ~line 1599)
- Modify: `app/services/batch_tag.py` (~line 135)
- Create: `tests/unit/test_search_sync.py`

**Important context:** Read these files before modifying. Line numbers are approximate — find the exact `db.commit()` calls. The pattern for every sync point is the same:

```python
# After the existing db.commit() and db.refresh() calls:
try:
    search_service = _get_search_service()
    if search_service:
        await search_service.index_tag(tag)  # or delete_tag(tag_id)
except Exception:
    logger.warning("meilisearch_sync_failed", tag_id=tag.tag_id)
```

**Step 1: Create a helper to get the search service safely**

Add to `app/services/search.py`:

```python
# Module-level reference set during app lifespan
_search_service: SearchService | None = None


def set_search_service(service: SearchService | None) -> None:
    """Set the module-level search service instance (called from lifespan)."""
    global _search_service
    _search_service = service


def get_search_service_instance() -> SearchService | None:
    """Get the current search service, or None if not initialized."""
    return _search_service
```

Update the lifespan in `app/main.py` to call `set_search_service(search_service)` after creating it, and `set_search_service(None)` on shutdown.

**Step 2: Write the failing test**

Create `tests/unit/test_search_sync.py`:

```python
"""Tests for Meilisearch sync helper."""

from unittest.mock import AsyncMock

import pytest

from app.models.tag import Tags
from app.services.search import SearchService, sync_tag_to_search


@pytest.mark.unit
class TestSyncTagToSearch:
    """Tests for the sync_tag_to_search helper."""

    async def test_indexes_tag_when_service_available(self):
        """sync_tag_to_search calls index_tag when service is available."""
        client = AsyncMock()
        index_mock = AsyncMock()
        client.index.return_value = index_mock
        service = SearchService(client)

        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        await sync_tag_to_search(tag, service=service)

        index_mock.add_documents.assert_awaited_once()

    async def test_no_error_when_service_unavailable(self):
        """sync_tag_to_search does nothing when service is None."""
        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        # Should not raise
        await sync_tag_to_search(tag, service=None)

    async def test_no_error_when_meilisearch_fails(self):
        """sync_tag_to_search swallows Meilisearch errors."""
        client = AsyncMock()
        index_mock = AsyncMock()
        index_mock.add_documents.side_effect = Exception("Connection refused")
        client.index.return_value = index_mock
        service = SearchService(client)

        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        # Should not raise
        await sync_tag_to_search(tag, service=service)
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_search_sync.py -v`
Expected: FAIL (sync_tag_to_search not defined)

**Step 4: Implement sync helpers**

Add to `app/services/search.py`:

```python
async def sync_tag_to_search(tag: Tags, *, service: SearchService | None = None) -> None:
    """Sync a tag to Meilisearch. Fire-and-forget — never raises.

    Args:
        tag: The tag to sync
        service: SearchService instance, or None to use module-level default
    """
    svc = service or _search_service
    if svc is None:
        return
    try:
        await svc.index_tag(tag)
    except Exception:
        logger.warning("meilisearch_sync_failed", tag_id=tag.tag_id)


async def sync_tag_delete_to_search(tag_id: int, *, service: SearchService | None = None) -> None:
    """Remove a tag from Meilisearch. Fire-and-forget — never raises.

    Args:
        tag_id: ID of the tag to remove
        service: SearchService instance, or None to use module-level default
    """
    svc = service or _search_service
    if svc is None:
        return
    try:
        await svc.delete_tag(tag_id)
    except Exception:
        logger.warning("meilisearch_sync_delete_failed", tag_id=tag_id)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_search_sync.py -v`
Expected: PASS

**Step 6: Add sync calls to tag write paths**

Read the exact code around each commit point before adding sync calls. The general pattern at each location:

In `app/api/v1/tags.py`:

**After create_tag commit (~line 1120):**
```python
    from app.services.search import sync_tag_to_search
    await sync_tag_to_search(new_tag)
```

**After update_tag commit (~line 1347):**
```python
    from app.services.search import sync_tag_to_search
    await sync_tag_to_search(tag)
    # If alias was set and tag_links migrated, also sync the canonical tag
    if tag_data.alias_of is not None and canonical_tag:
        await sync_tag_to_search(canonical_tag)
```

**After delete_tag commit (~line 1370):**
```python
    from app.services.search import sync_tag_delete_to_search
    await sync_tag_delete_to_search(tag_id)
```

In `app/api/v1/images.py`:

**After add_tag_to_image commit (~line 1533):**
```python
    from app.services.search import sync_tag_to_search
    # Re-fetch the tag to get updated usage_count
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == resolved_tag_id))
    updated_tag = tag_result.scalar_one_or_none()
    if updated_tag:
        await sync_tag_to_search(updated_tag)
```

**After remove_tag_from_image commit (~line 1599):**
```python
    from app.services.search import sync_tag_to_search
    tag_result = await db.execute(select(Tags).where(Tags.tag_id == resolved_tag_id))
    updated_tag = tag_result.scalar_one_or_none()
    if updated_tag:
        await sync_tag_to_search(updated_tag)
```

In `app/services/batch_tag.py`:

**After batch_add_tags commit (~line 135):**
```python
    from app.services.search import sync_tag_to_search
    # Sync all affected tags (usage_count changed)
    for tag_id in {item.tag_id for item in response.added}:
        tag_result = await db.execute(select(Tags).where(Tags.tag_id == tag_id))
        tag = tag_result.scalar_one_or_none()
        if tag:
            await sync_tag_to_search(tag)
```

**IMPORTANT:** Read each file carefully before editing. The line numbers above are approximate — locate the exact `db.commit()` and `db.refresh()` calls. Add the sync call AFTER both commit and refresh.

**Step 7: Run all tests to verify nothing is broken**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
git add app/services/search.py app/api/v1/tags.py app/api/v1/images.py app/services/batch_tag.py app/main.py tests/unit/test_search_sync.py
git commit -m "feat: sync tag writes to Meilisearch"
```

---

### Task 9: Create Reindex Script

**Files:**
- Create: `scripts/reindex_search.py`

**Step 1: Create the script**

Create `scripts/reindex_search.py`:

```python
"""Bulk reindex all tags from MySQL to Meilisearch.

Usage:
    uv run python scripts/reindex_search.py
    uv run python scripts/reindex_search.py --batch-size 500

Idempotent — safe to run anytime. Meilisearch upserts by primary key.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from meilisearch_python_sdk import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.tag import Tags
from app.services.search import SearchService, configure_tags_index


async def reindex_tags(batch_size: int = 1000) -> None:
    """Reindex all tags from MySQL to Meilisearch."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    client = AsyncClient(
        url=settings.MEILISEARCH_URL,
        api_key=settings.MEILISEARCH_API_KEY,
    )

    try:
        # Configure index settings
        await configure_tags_index(client)
        service = SearchService(client)

        async with AsyncSession(engine) as db:
            # Get total count
            count_result = await db.execute(select(func.count(Tags.tag_id)))
            total = count_result.scalar() or 0
            print(f"Reindexing {total} tags...")

            # Batch fetch and index
            indexed = 0
            offset = 0
            start = time.monotonic()

            while offset < total:
                result = await db.execute(
                    select(Tags)
                    .order_by(Tags.tag_id)
                    .offset(offset)
                    .limit(batch_size)
                )
                tags = list(result.scalars().all())

                if not tags:
                    break

                await service.index_tags(tags)
                indexed += len(tags)
                offset += batch_size
                print(f"  Indexed {indexed}/{total} tags...")

            elapsed = time.monotonic() - start
            print(f"Done. Indexed {indexed} tags in {elapsed:.1f}s")

    finally:
        await client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindex tags to Meilisearch")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for indexing")
    args = parser.parse_args()

    asyncio.run(reindex_tags(batch_size=args.batch_size))


if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add scripts/reindex_search.py
git commit -m "feat: add bulk reindex script for Meilisearch"
```

---

### Task 10: Integration Tests

These tests run against a real Meilisearch instance. They verify the full flow:
indexing, searching, and result ordering.

**Files:**
- Create: `tests/integration/test_search_integration.py`

**Prerequisites:** Meilisearch must be running (`docker compose up meilisearch`).

**Step 1: Create integration test file**

Create `tests/integration/test_search_integration.py`:

```python
"""Integration tests for Meilisearch search service.

These tests require a running Meilisearch instance.
Run: docker compose up meilisearch
"""

import asyncio
import os
import time

import pytest
from meilisearch_python_sdk import AsyncClient

from app.config import TagType
from app.models.tag import Tags
from app.services.search import SearchService, configure_tags_index

MEILISEARCH_URL = os.getenv("MEILISEARCH_URL", "http://localhost:7700")
MEILISEARCH_KEY = os.getenv("MEILI_MASTER_KEY", "dev_master_key")

# Use a test-specific index prefix to avoid colliding with dev data
TEST_INDEX_NAME = "tags_test"


@pytest.fixture
async def meilisearch_client():
    """Create a Meilisearch client, skip if unavailable."""
    client = AsyncClient(url=MEILISEARCH_URL, api_key=MEILISEARCH_KEY)
    try:
        await client.health()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"Meilisearch not available at {MEILISEARCH_URL}: {exc}")

    yield client

    # Cleanup: delete test index
    try:
        await client.index(TEST_INDEX_NAME).delete()
    except Exception:
        pass
    await client.aclose()


@pytest.fixture
async def search_service(meilisearch_client):
    """Create a SearchService with a test index."""
    # Temporarily override the index name for tests
    import app.services.search as search_module

    original_name = search_module.TAGS_INDEX_NAME
    search_module.TAGS_INDEX_NAME = TEST_INDEX_NAME

    await configure_tags_index(meilisearch_client)
    service = SearchService(meilisearch_client)

    yield service

    search_module.TAGS_INDEX_NAME = original_name


async def _wait_for_indexing(client: AsyncClient, index_name: str, timeout: float = 5.0):
    """Wait for Meilisearch to finish processing all pending tasks."""
    index = client.index(index_name)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        tasks = await client.get_tasks({"indexUids": [index_name], "statuses": ["enqueued", "processing"]})
        if not tasks.results:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("Meilisearch did not finish indexing in time")


@pytest.mark.integration
class TestSearchServiceIntegration:
    """Integration tests against a real Meilisearch instance."""

    async def test_index_and_search_tag(self, search_service, meilisearch_client):
        """Index a tag and find it via search."""
        tag = Tags(
            tag_id=1,
            title="Sakura Kinomoto",
            desc="Main character from Cardcaptor Sakura",
            type=TagType.CHARACTER,
            usage_count=100,
        )
        await search_service.index_tag(tag)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("sakura")
        assert 1 in result.tag_ids

    async def test_prefix_search(self, search_service, meilisearch_client):
        """Partial prefix matches work (typeahead)."""
        tags = [
            Tags(tag_id=10, title="Sakura Kinomoto", type=TagType.CHARACTER, usage_count=100),
            Tags(tag_id=11, title="Sakurajima Mai", type=TagType.CHARACTER, usage_count=50),
            Tags(tag_id=12, title="Blue Sky", type=TagType.THEME, usage_count=10),
        ]
        await search_service.index_tags(tags)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("saku")
        assert 10 in result.tag_ids
        assert 11 in result.tag_ids
        assert 12 not in result.tag_ids

    async def test_type_filter(self, search_service, meilisearch_client):
        """Type filter restricts results to matching tag type."""
        tags = [
            Tags(tag_id=20, title="School Uniform", type=TagType.THEME, usage_count=50),
            Tags(tag_id=21, title="School Days", type=TagType.SOURCE, usage_count=30),
        ]
        await search_service.index_tags(tags)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("school", type_filter=TagType.THEME)
        assert 20 in result.tag_ids
        assert 21 not in result.tag_ids

    async def test_exclude_aliases(self, search_service, meilisearch_client):
        """Alias exclusion filter works."""
        tags = [
            Tags(tag_id=30, title="Choker", type=TagType.THEME, usage_count=40, alias_of=None),
            Tags(tag_id=31, title="Collar", type=TagType.THEME, usage_count=0, alias_of=30),
        ]
        await search_service.index_tags(tags)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("c", exclude_aliases=True)
        assert 30 in result.tag_ids
        assert 31 not in result.tag_ids

    async def test_delete_tag_removes_from_search(self, search_service, meilisearch_client):
        """Deleted tags no longer appear in search results."""
        tag = Tags(tag_id=40, title="Deleted Tag", type=TagType.THEME, usage_count=0)
        await search_service.index_tag(tag)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        # Verify it's findable
        result = await search_service.search_tags("deleted")
        assert 40 in result.tag_ids

        # Delete and verify it's gone
        await search_service.delete_tag(40)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("deleted")
        assert 40 not in result.tag_ids

    async def test_usage_count_affects_ranking(self, search_service, meilisearch_client):
        """Higher usage_count tags rank higher when relevance is equal."""
        tags = [
            Tags(tag_id=50, title="Swimsuit", type=TagType.THEME, usage_count=5),
            Tags(tag_id=51, title="Swimsuit", type=TagType.THEME, usage_count=500),
        ]
        await search_service.index_tags(tags)
        await _wait_for_indexing(meilisearch_client, TEST_INDEX_NAME)

        result = await search_service.search_tags("swimsuit")
        assert result.tag_ids[0] == 51  # Higher usage_count first
```

**Step 2: Run integration tests**

Run: `uv run pytest tests/integration/test_search_integration.py -v`
Expected: PASS (or skip if Meilisearch is not running)

**Step 3: Commit**

```bash
git add tests/integration/test_search_integration.py
git commit -m "test: add Meilisearch integration tests"
```

---

### Task 11: Add mypy Override for meilisearch-python-sdk

**Files:**
- Modify: `pyproject.toml:168-173` (mypy overrides)

**Step 1: Add override**

Check if `meilisearch_python_sdk` has type stubs. If mypy complains, add to the existing overrides:

```toml
[[tool.mypy.overrides]]
module = [
    "aiomysql.*",
    "arq.*",
    "PIL.*",
    "meilisearch_python_sdk.*",
]
ignore_missing_imports = true
```

**Step 2: Run type checking**

Run: `uv run mypy app/services/search.py app/core/meilisearch.py app/api/v1/search.py`
Expected: No errors (or only pre-existing ones)

**Step 3: Commit (if changes needed)**

```bash
git add pyproject.toml
git commit -m "chore: add mypy override for meilisearch-python-sdk"
```

---

### Task 12: Final Verification

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 2: Start services and test manually**

```bash
docker compose up -d meilisearch
uv run python scripts/reindex_search.py
curl -s "http://localhost:8000/api/v1/search?q=sakura" | python -m json.tool
```

**Step 3: Compare with existing search**

```bash
# Meilisearch-backed search
curl -s "http://localhost:8000/api/v1/search?q=sakura" | python -m json.tool

# Existing MySQL fulltext search
curl -s "http://localhost:8000/api/v1/tags?search=sakura" | python -m json.tool
```

**Step 4: Final commit if any cleanup needed**

---

## Notes

- **The existing `GET /api/v1/tags?search=` endpoint is NOT modified.** Both search paths run in parallel.
- **Meilisearch is optional.** If the service is unavailable at startup, the app logs a warning and continues. The `/api/v1/search` endpoint will return errors, but all other endpoints work normally.
- **Sync is fire-and-forget.** If Meilisearch is down during a tag write, the MySQL write succeeds and the data will be stale in search until the next `reindex_search.py` run.
- **Integration tests skip gracefully** if Meilisearch is not running.

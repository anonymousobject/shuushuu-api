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
MEILISEARCH_KEY = os.getenv("MEILISEARCH_API_KEY") or os.getenv("MEILI_MASTER_KEY", "dev_master_key")

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
        await client.delete_index_if_exists(TEST_INDEX_NAME)
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
    """Wait for Meilisearch to finish processing all pending tasks for the index."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        tasks = await client.get_tasks(index_ids=[index_name])
        pending = [t for t in tasks.results if t.status in ("enqueued", "processing")]
        if not pending:
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

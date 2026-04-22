"""Unit tests for the search service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import TagType
from app.models.tag import Tags
from app.services.search import SearchService, _tag_to_document

TAGS_INDEX_NAME = "tags"


def _make_mock_client() -> MagicMock:
    """Create a mock Meilisearch client with an index mock.

    AsyncClient.index() is synchronous (returns AsyncIndex without awaiting),
    so we use MagicMock for the client and a regular return_value.
    The index mock itself uses AsyncMock for its async methods.
    """
    client = MagicMock()
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
            "external_urls": [],
        }

    async def test_index_tag_with_external_urls(self):
        """index_tag includes external_urls in the document."""
        client = _make_mock_client()
        service = SearchService(client)
        tag = _make_tag()
        urls = ["https://www.pixiv.net/en/users/124261821"]

        await service.index_tag(tag, external_urls=urls)

        index_mock = client.index(TAGS_INDEX_NAME)
        docs = index_mock.add_documents.call_args[0][0]
        assert docs[0]["external_urls"] == urls

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
class TestTagToDocument:
    """Tests for _tag_to_document helper."""

    def test_alias_tag_uses_parent_usage_count(self):
        """Alias tags should use the parent's usage_count for ranking."""
        alias_tag = _make_tag(tag_id=10, alias_of=99, usage_count=0)
        doc = _tag_to_document(alias_tag, parent_usage_count=4545)
        assert doc["usage_count"] == 4545

    def test_non_alias_tag_uses_own_usage_count(self):
        """Non-alias tags use their own usage_count."""
        tag = _make_tag(tag_id=10, usage_count=42)
        doc = _tag_to_document(tag)
        assert doc["usage_count"] == 42

    def test_alias_tag_without_parent_count_uses_own(self):
        """If parent_usage_count not provided, alias uses its own count."""
        alias_tag = _make_tag(tag_id=10, alias_of=99, usage_count=0)
        doc = _tag_to_document(alias_tag)
        assert doc["usage_count"] == 0

    def test_includes_external_urls(self):
        """external_urls are included in the document when provided."""
        tag = _make_tag()
        urls = ["https://www.pixiv.net/en/users/124261821", "https://twitter.com/artist"]
        doc = _tag_to_document(tag, external_urls=urls)
        assert doc["external_urls"] == urls

    def test_defaults_to_empty_external_urls(self):
        """external_urls defaults to empty list when not provided."""
        tag = _make_tag()
        doc = _tag_to_document(tag)
        assert doc["external_urls"] == []


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
            hits=[],
            estimated_total_hits=0,
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
            hits=[],
            estimated_total_hits=0,
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
            hits=[],
            estimated_total_hits=0,
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
            hits=[],
            estimated_total_hits=0,
        )

        result = await service.search_tags("nonexistent")

        assert result.tag_ids == []
        assert result.total == 0

"""Unit tests for the search service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import TagType
from app.models.tag import Tags
from app.services.search import SearchService

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

"""Tests for Meilisearch sync helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.tag import Tags
from app.services.search import SearchService, sync_tag_delete_to_search, sync_tag_to_search


def _make_mock_client() -> MagicMock:
    """Create a mock Meilisearch client with an index mock."""
    client = MagicMock()
    index_mock = AsyncMock()
    client.index.return_value = index_mock
    return client


@pytest.mark.unit
class TestSyncTagToSearch:
    """Tests for the sync_tag_to_search helper."""

    async def test_indexes_tag_when_service_available(self):
        """sync_tag_to_search calls index_tag when service is available."""
        client = _make_mock_client()
        service = SearchService(client)

        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        await sync_tag_to_search(tag, service=service)

        index_mock = client.index.return_value
        index_mock.add_documents.assert_awaited_once()

    async def test_no_error_when_service_unavailable(self):
        """sync_tag_to_search does nothing when service is None."""
        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        # Should not raise
        await sync_tag_to_search(tag, service=None)

    async def test_no_error_when_meilisearch_fails(self):
        """sync_tag_to_search swallows Meilisearch errors."""
        client = _make_mock_client()
        index_mock = client.index.return_value
        index_mock.add_documents.side_effect = Exception("Connection refused")
        service = SearchService(client)

        tag = Tags(tag_id=1, title="Test", type=1, usage_count=0)
        # Should not raise
        await sync_tag_to_search(tag, service=service)


@pytest.mark.unit
class TestSyncTagDeleteToSearch:
    """Tests for the sync_tag_delete_to_search helper."""

    async def test_deletes_tag_when_service_available(self):
        """sync_tag_delete_to_search calls delete_tag when service is available."""
        client = _make_mock_client()
        service = SearchService(client)

        await sync_tag_delete_to_search(42, service=service)

        index_mock = client.index.return_value
        index_mock.delete_document.assert_awaited_once_with("42")

    async def test_no_error_when_service_unavailable(self):
        """sync_tag_delete_to_search does nothing when service is None."""
        await sync_tag_delete_to_search(42, service=None)

    async def test_no_error_when_meilisearch_fails(self):
        """sync_tag_delete_to_search swallows Meilisearch errors."""
        client = _make_mock_client()
        index_mock = client.index.return_value
        index_mock.delete_document.side_effect = Exception("Connection refused")
        service = SearchService(client)

        await sync_tag_delete_to_search(42, service=service)

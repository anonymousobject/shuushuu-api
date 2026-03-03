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

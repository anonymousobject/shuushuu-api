"""Unit tests for app/services/iqdb.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.iqdb import check_iqdb_similarity_by_hash


class TestCheckIqdbSimilarityByHash:
    """Tests for check_iqdb_similarity_by_hash."""

    @pytest.mark.asyncio
    async def test_passes_hash_in_query_string(self):
        """The function GETs /query with ?h=<hash>, no body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        captured = {}

        async def fake_get(url, params=None):
            captured["url"] = url
            captured["params"] = params
            return mock_response

        mock_client = MagicMock()
        mock_client.__aenter__.return_value.get = fake_get
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            await check_iqdb_similarity_by_hash("iqdb_deadbeef", threshold=50.0)

        assert "/query" in captured["url"]
        assert captured["params"] == {"h": "iqdb_deadbeef"}

    @pytest.mark.asyncio
    async def test_filters_by_threshold(self):
        """Results below threshold are dropped; remaining are mapped."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"post_id": 1, "score": 95.0, "hash": "x", "signature": {}},
            {"post_id": 2, "score": 30.0, "hash": "x", "signature": {}},
            {"post_id": 3, "score": 60.0, "hash": "x", "signature": {}},
        ]

        mock_client = MagicMock()
        mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await check_iqdb_similarity_by_hash(
                "iqdb_deadbeef", threshold=50.0
            )

        assert results == [
            {"image_id": 1, "score": 95.0},
            {"image_id": 3, "score": 60.0},
        ]

    @pytest.mark.asyncio
    async def test_returns_empty_on_iqdb_error(self):
        """Non-200 response yields an empty list (don't 500 the route)."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = MagicMock()
        mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await check_iqdb_similarity_by_hash("iqdb_deadbeef", threshold=50.0)

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self):
        """A RequestError yields an empty list."""
        import httpx

        mock_client = MagicMock()
        mock_client.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.RequestError("connection refused")
        )
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await check_iqdb_similarity_by_hash("iqdb_deadbeef", threshold=50.0)

        assert results == []

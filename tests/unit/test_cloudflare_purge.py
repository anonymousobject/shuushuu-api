"""Tests for Cloudflare cache purge service."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.services.cloudflare import purge_cache_by_urls


def _make_mock_client(raise_for_status=None):
    """Build a mock httpx.AsyncClient for use in tests.

    httpx.Response is sync — MagicMock, not AsyncMock, so .text/.json
    don't become coroutines.
    """
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json = lambda: {"success": True, "errors": []}
    mock_response.raise_for_status = raise_for_status or (lambda: None)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


@pytest.mark.unit
class TestPurgeCacheByUrls:
    async def test_no_op_when_urls_empty(self):
        """Empty URL list is a no-op (no HTTP calls)."""
        with patch("app.services.cloudflare.httpx.AsyncClient") as client_cls:
            await purge_cache_by_urls([])
            client_cls.assert_not_called()

    async def test_raises_when_credentials_missing(self, monkeypatch):
        """Missing Cloudflare config is a misconfiguration — raise, don't silently no-op."""
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "")
        with pytest.raises(RuntimeError, match="CLOUDFLARE_ZONE_ID"):
            await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])

    async def test_posts_to_cloudflare_api(self, monkeypatch):
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        mock_client = _make_mock_client()
        with patch("app.services.cloudflare.httpx.AsyncClient", return_value=mock_client):
            await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])

        mock_client.post.assert_awaited_once()
        call = mock_client.post.await_args
        assert "zones/zone/purge_cache" in call.args[0]
        assert call.kwargs["json"] == {"files": ["https://cdn.example.com/x.jpg"]}
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok"

    async def test_batches_urls_in_groups_of_30(self, monkeypatch):
        """Cloudflare free plan accepts max 30 URLs per call — we chunk accordingly."""
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        mock_client = _make_mock_client()
        urls = [f"https://cdn.example.com/{i}.jpg" for i in range(65)]
        with patch("app.services.cloudflare.httpx.AsyncClient", return_value=mock_client):
            await purge_cache_by_urls(urls)

        # 65 URLs → 30 + 30 + 5 = 3 calls
        assert mock_client.post.await_count == 3
        first_batch = mock_client.post.await_args_list[0].kwargs["json"]["files"]
        last_batch = mock_client.post.await_args_list[2].kwargs["json"]["files"]
        assert len(first_batch) == 30
        assert len(last_batch) == 5

    async def test_logs_and_raises_on_cloudflare_error(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone")

        # httpx.HTTPStatusError requires a real Request object — passing None
        # TypeErrors at construction time on httpx ≥0.24.
        fake_request = httpx.Request("POST", "https://api.cloudflare.com/")
        fake_response = MagicMock(spec=httpx.Response)
        fake_response.status_code = 400
        fake_response.text = '{"success": false, "errors": [{"message": "bad"}]}'

        def raise_for_status():
            raise httpx.HTTPStatusError("400", request=fake_request, response=fake_response)

        mock_client = _make_mock_client(raise_for_status=raise_for_status)

        with caplog.at_level(logging.ERROR), patch(
            "app.services.cloudflare.httpx.AsyncClient", return_value=mock_client
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await purge_cache_by_urls(["https://cdn.example.com/x.jpg"])

        assert any("r2_cdn_purge_failed" in r.message for r in caplog.records)

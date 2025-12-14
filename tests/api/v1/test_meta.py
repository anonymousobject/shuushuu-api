"""
Tests for meta API endpoints.

These tests cover the /api/v1/meta endpoints including:
- Get public configuration
"""

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.api
class TestGetPublicConfig:
    """Tests for GET /api/v1/meta/config endpoint."""

    async def test_get_public_config(self, client: AsyncClient):
        """Test getting public configuration settings."""
        response = await client.get("/api/v1/meta/config")
        assert response.status_code == 200

        data = response.json()

        # Verify all expected fields are present
        assert "max_search_tags" in data
        assert "max_image_size" in data
        assert "max_avatar_size" in data
        assert "upload_delay_seconds" in data
        assert "search_delay_seconds" in data
        assert "tag_types" in data

    async def test_config_values_match_settings(self, client: AsyncClient):
        """Test that returned values match the settings from app.config."""
        response = await client.get("/api/v1/meta/config")
        assert response.status_code == 200

        data = response.json()

        # Verify values match settings
        assert data["max_search_tags"] == settings.MAX_SEARCH_TAGS
        assert data["max_image_size"] == settings.MAX_IMAGE_SIZE
        assert data["max_avatar_size"] == settings.MAX_AVATAR_SIZE
        assert data["upload_delay_seconds"] == settings.UPLOAD_DELAY_SECONDS
        assert data["search_delay_seconds"] == settings.SEARCH_DELAY_SECONDS

    async def test_config_response_structure(self, client: AsyncClient):
        """Test that the response structure matches the PublicConfig model."""
        response = await client.get("/api/v1/meta/config")
        assert response.status_code == 200

        data = response.json()

        # Verify data types
        assert isinstance(data["max_search_tags"], int)
        assert isinstance(data["max_image_size"], int)
        assert isinstance(data["max_avatar_size"], int)
        assert isinstance(data["upload_delay_seconds"], int)
        assert isinstance(data["search_delay_seconds"], int)
        assert isinstance(data["tag_types"], dict)

        # Verify positive values
        assert data["max_search_tags"] > 0
        assert data["max_image_size"] > 0
        assert data["max_avatar_size"] > 0
        assert data["upload_delay_seconds"] >= 0
        assert data["search_delay_seconds"] >= 0

    async def test_tag_types_field(self, client: AsyncClient):
        """Test that tag_types field contains expected tag type mappings."""
        response = await client.get("/api/v1/meta/config")
        assert response.status_code == 200

        data = response.json()
        tag_types = data["tag_types"]

        # Verify tag_types is a dictionary
        assert isinstance(tag_types, dict)

        # Verify expected tag type entries are present
        # Based on TagType constants: THEME=1, SOURCE=2, ARTIST=3, CHARACTER=4
        assert "1" in tag_types or 1 in tag_types
        assert "2" in tag_types or 2 in tag_types
        assert "3" in tag_types or 3 in tag_types
        assert "4" in tag_types or 4 in tag_types

        # Get the actual values (handling both string and int keys)
        theme = tag_types.get("1") or tag_types.get(1)
        source = tag_types.get("2") or tag_types.get(2)
        artist = tag_types.get("3") or tag_types.get(3)
        character = tag_types.get("4") or tag_types.get(4)

        # Verify the values match expected tag type names
        assert theme == "Theme"
        assert source == "Source"
        assert artist == "Artist"
        assert character == "Character"

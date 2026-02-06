"""Tests for banner preference API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize, BannerTheme, UserBannerPins, UserBannerPreferences


@pytest.mark.api
class TestGetPreferences:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/v1/banners/preferences")
        assert response.status_code == 401

    async def test_returns_defaults(self, authenticated_client: AsyncClient):
        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        data = response.json()
        assert data["preferred_size"] == "small"
        assert data["pins"] == []

    async def test_returns_stored_preferences(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        prefs = UserBannerPreferences(user_id=sample_user.user_id, preferred_size=BannerSize.large)
        db_session.add(prefs)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        assert response.json()["preferred_size"] == "large"

    async def test_returns_pins_with_banner_data(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        banner = Banners(
            name="pinned", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="pin.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=sample_user.user_id, size=BannerSize.small,
            theme=BannerTheme.dark, banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        response = await authenticated_client.get("/api/v1/banners/preferences")
        assert response.status_code == 200
        pins = response.json()["pins"]
        assert len(pins) == 1
        assert pins[0]["banner"]["name"] == "pinned"


@pytest.mark.api
class TestUpdatePreferences:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "large"},
        )
        assert response.status_code == 401

    async def test_updates_size(self, authenticated_client: AsyncClient):
        response = await authenticated_client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "large"},
        )
        assert response.status_code == 200
        assert response.json()["preferred_size"] == "large"

    async def test_rejects_invalid_size(self, authenticated_client: AsyncClient):
        response = await authenticated_client.patch(
            "/api/v1/banners/preferences", json={"preferred_size": "huge"},
        )
        assert response.status_code == 422


@pytest.mark.api
class TestPinBanner:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": 1},
        )
        assert response.status_code == 401

    async def test_pins_banner(
        self, authenticated_client: AsyncClient, db_session: AsyncSession,
    ):
        banner = Banners(
            name="to_pin", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="pin.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": banner.banner_id},
        )
        assert response.status_code == 200

    async def test_rejects_nonexistent_banner(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": 99999},
        )
        assert response.status_code == 404

    async def test_rejects_size_mismatch(
        self, authenticated_client: AsyncClient, db_session: AsyncSession,
    ):
        banner = Banners(
            name="medium_b", size=BannerSize.medium, supports_dark=True,
            supports_light=True, full_image="m.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/dark",
            json={"banner_id": banner.banner_id},
        )
        assert response.status_code == 400

    async def test_rejects_invalid_size_path(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/huge/dark",
            json={"banner_id": 1},
        )
        assert response.status_code == 422

    async def test_rejects_invalid_theme_path(self, authenticated_client: AsyncClient):
        response = await authenticated_client.put(
            "/api/v1/banners/preferences/pins/small/neon",
            json={"banner_id": 1},
        )
        assert response.status_code == 422


@pytest.mark.api
class TestUnpinBanner:
    async def test_requires_auth(self, client: AsyncClient):
        response = await client.delete("/api/v1/banners/preferences/pins/small/dark")
        assert response.status_code == 401

    async def test_removes_pin(
        self, authenticated_client: AsyncClient, db_session: AsyncSession, sample_user,
    ):
        banner = Banners(
            name="unpin_me", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="u.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=sample_user.user_id, size=BannerSize.small,
            theme=BannerTheme.dark, banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        response = await authenticated_client.delete(
            "/api/v1/banners/preferences/pins/small/dark",
        )
        assert response.status_code == 204

    async def test_404_when_no_pin(self, authenticated_client: AsyncClient):
        response = await authenticated_client.delete(
            "/api/v1/banners/preferences/pins/small/dark",
        )
        assert response.status_code == 404


@pytest.mark.api
class TestCurrentBannerWithAuth:
    async def test_anonymous_still_works(
        self, client_real_redis: AsyncClient, db_session: AsyncSession,
    ):
        """Anonymous request without auth works as before."""
        banner = Banners(
            name="anon", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="anon.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        response = await client_real_redis.get(
            "/api/v1/banners/current", params={"theme": "dark", "size": "small"},
        )
        assert response.status_code == 200

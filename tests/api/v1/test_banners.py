"""Tests for banners API endpoints."""

import json
from httpx import AsyncClient

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import BannerSize, Banners


@pytest.mark.api
class TestCurrentBanner:
    async def test_current_banner_no_banners_returns_expected_404(
        self,
        client_real_redis: AsyncClient,
    ) -> None:
        response = await client_real_redis.get(
            "/api/v1/banners/current",
            params={"theme": "dark", "size": "medium"},
        )
        assert response.status_code == 404
        # Distinguish from missing route 404
        assert response.json().get("detail") != "Not Found"

    async def test_current_banner_cache_hit(
        self,
        client_real_redis: AsyncClient,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ) -> None:
        await redis_client.set(
            "banner:current:dark:medium",
            json.dumps(
                {
                    "banner_id": 1,
                    "name": "cached",
                    "author": None,
                    "size": "medium",
                    "supports_dark": True,
                    "supports_light": True,
                    "full_image": "x.png",
                    "left_image": None,
                    "middle_image": None,
                    "right_image": None,
                }
            ),
        )

        response = await client_real_redis.get(
            "/api/v1/banners/current",
            params={"theme": "dark", "size": "medium"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "cached"
        assert data["is_full"] is True
        assert data["full_image_url"] is not None


@pytest.mark.api
class TestListBanners:
    async def test_list_banners_returns_active(
        self,
        client_real_redis: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        active_banner = Banners(
            name="list_banner",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=True,
            full_image="db.png",
            active=True,
        )
        inactive_banner = Banners(
            name="inactive_banner",
            size=BannerSize.medium,
            supports_dark=True,
            supports_light=True,
            full_image="inactive.png",
            active=False,
        )

        db_session.add(active_banner)
        db_session.add(inactive_banner)
        await db_session.commit()

        response = await client_real_redis.get("/api/v1/banners")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert data["page"] == 1
        assert data["per_page"] == 20
        assert data["total"] == 1

        items = data["items"]
        assert isinstance(items, list)
        assert any(item["name"] == "list_banner" for item in items)
        assert all(item["name"] != "inactive_banner" for item in items)

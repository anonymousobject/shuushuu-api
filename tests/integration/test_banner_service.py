"""Integration tests for Banner service."""

import json

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.misc import BannerSize, Banners


@pytest.mark.integration
class TestGetCurrentBanner:
    async def test_cache_hit_returns_cached(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
    ) -> None:
        from app.services.banner import BANNER_CACHE_KEY_PREFIX, get_current_banner

        cache_key = f"{BANNER_CACHE_KEY_PREFIX}dark:small"
        await redis_client.set(
            cache_key,
            json.dumps(
                {
                    "banner_id": 1,
                    "name": "cached",
                    "author": None,
                    "size": "small",
                    "supports_dark": True,
                    "supports_light": True,
                    "full_image": "x.png",
                    "left_image": None,
                    "middle_image": None,
                    "right_image": None,
                }
            ),
        )

        result = await get_current_banner("dark", "small", db_session, redis_client)
        assert result.banner_id == 1
        assert result.name == "cached"

    async def test_cache_miss_selects_valid_banner_and_sets_cache(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.banner import BANNER_CACHE_KEY_PREFIX, get_current_banner

        monkeypatch.setattr(settings, "BANNER_CACHE_TTL", 5)
        monkeypatch.setattr(settings, "BANNER_CACHE_TTL_JITTER", 2)

        valid = Banners(
            name="db_banner",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="db.png",
            active=True,
        )
        # Invalid layout should be ignored by the service
        invalid = Banners(
            name="invalid_banner",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="full.png",
            left_image="left.png",
            active=True,
        )

        db_session.add(valid)
        db_session.add(invalid)
        await db_session.commit()
        await db_session.refresh(valid)

        result = await get_current_banner("dark", "small", db_session, redis_client)
        assert result.banner_id == valid.banner_id
        assert result.name == "db_banner"

        cache_key = f"{BANNER_CACHE_KEY_PREFIX}dark:small"
        cached = await redis_client.get(cache_key)
        assert cached is not None

        ttl = await redis_client.ttl(cache_key)
        # TTL should be in [5, 7] but allow 1s slack for clock tick
        assert 4 <= ttl <= 7

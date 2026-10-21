"""The anonymous feed is answered from the DB when Redis is down.

Both feed caches are best-effort: a Redis error is logged and treated as a cache
miss (reads) or a skipped store (writes). This module overrides conftest's
``redis_client`` with a real Redis client pointed at a port nothing listens on, so
every command raises a genuine ConnectionError, and ``client_real_redis`` serves
the app wired to that dead client.
"""

import logging
import socket
from collections.abc import AsyncGenerator

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import Images
from app.services.feed_count_cache import get_feed_counts, get_filtered_count
from app.services.feed_response_cache import cache_feed_response, get_cached_feed_response
from tests.api.v1.test_feed_count import _img, _link, _tag, _user


@pytest.fixture
async def redis_client() -> AsyncGenerator[redis.Redis]:  # type: ignore[type-arg]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    yield client
    await client.aclose()


def _logged(caplog: pytest.LogCaptureFixture, event: str) -> bool:
    return any(event in record.message for record in caplog.records)


@pytest.mark.api
class TestFeedWithRedisDown:
    async def test_default_feed_served_from_db_and_warnings_logged(
        self,
        client_real_redis: AsyncClient,
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ):
        owner = await _user(db_session, "rdDefault")
        await _img(db_session, owner, "a0" + "0" * 30, 1)
        await _img(db_session, owner, "a1" + "1" * 30, 1)

        with caplog.at_level(logging.WARNING):
            response = await client_real_redis.get("/api/v1/images/?per_page=5")

        assert response.status_code == 200
        body = response.json()
        assert len(body["images"]) == 2
        assert body["total"] == 2
        assert _logged(caplog, "feed_response_cache_redis_error")
        assert _logged(caplog, "feed_count_cache_redis_error")

    async def test_filtered_feed_served_from_db(
        self,
        client_real_redis: AsyncClient,
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ):
        owner = await _user(db_session, "rdFiltered")
        tag = await _tag(db_session, "redis-down")
        tagged = await _img(db_session, owner, "b0" + "0" * 30, 1)
        await _img(db_session, owner, "b1" + "1" * 30, 1)
        await _link(db_session, tag, tagged)

        with caplog.at_level(logging.WARNING):
            response = await client_real_redis.get(f"/api/v1/images/?tags={tag.tag_id}")

        assert response.status_code == 200
        body = response.json()
        assert len(body["images"]) == 1
        assert body["total"] == 1
        assert _logged(caplog, "feed_count_cache_redis_error")


@pytest.mark.api
class TestFeedCacheHelpersWithRedisDown:
    async def test_get_feed_counts_computes_from_db(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        caplog: pytest.LogCaptureFixture,
    ):
        owner = await _user(db_session, "rdCounts")
        await _img(db_session, owner, "c0" + "0" * 30, 1)
        await _img(db_session, owner, "c1" + "1" * 30, 0)  # hidden
        await _img(db_session, owner, "c2" + "2" * 30, -1)  # repost

        with caplog.at_level(logging.WARNING):
            assert await get_feed_counts(db_session, redis_client) == (3, 1, 1)
        assert _logged(caplog, "feed_count_cache_redis_error")

    async def test_get_filtered_count_computes_from_db(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        caplog: pytest.LogCaptureFixture,
    ):
        owner = await _user(db_session, "rdFilteredCount")
        await _img(db_session, owner, "d0" + "0" * 30, 1)
        await _img(db_session, owner, "d1" + "1" * 30, 1)
        count_query = select(func.count()).select_from(Images)

        with caplog.at_level(logging.WARNING):
            assert await get_filtered_count(db_session, count_query, redis_client) == 2
        assert _logged(caplog, "feed_count_cache_redis_error")

    async def test_response_cache_read_is_a_miss(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        caplog: pytest.LogCaptureFixture,
    ):
        with caplog.at_level(logging.WARNING):
            assert await get_cached_feed_response(redis_client, "feed:response:x") is None
        assert _logged(caplog, "feed_response_cache_redis_error")

    async def test_response_cache_write_is_skipped(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        caplog: pytest.LogCaptureFixture,
    ):
        with caplog.at_level(logging.WARNING):
            await cache_feed_response(redis_client, "feed:response:x", "{}")
        assert _logged(caplog, "feed_response_cache_redis_error")

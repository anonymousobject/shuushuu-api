"""Tests for the shared per-process Redis connection pool (#381).

Before this, app.core.redis.get_redis called redis.from_url(...) on every
dependency resolution and closed the client in `finally`, so every request
that depended on it paid a fresh TCP connect -- the dominant cause of the
"Too many open files" errors during the 2026-09-05 scraper waves. Now
app/main.py's lifespan creates one redis.asyncio.ConnectionPool per process
(via create_redis_pool), stores it on app.state.redis_pool, and get_redis
binds a lightweight client to that shared pool instead of opening its own.
"""

import pytest
import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.core.redis import create_redis_pool, get_redis


@pytest.mark.unit
class TestCreateRedisPool:
    """create_redis_pool() builds the pool the lifespan stores on app.state."""

    async def test_honours_max_connections_setting(self, monkeypatch, test_redis_url):
        monkeypatch.setattr(settings, "REDIS_URL", test_redis_url)
        monkeypatch.setattr(settings, "REDIS_MAX_CONNECTIONS", 7)

        pool = create_redis_pool()
        try:
            assert pool.max_connections == 7
        finally:
            await pool.disconnect()

    async def test_pool_is_really_connectable(self, monkeypatch, test_redis_url):
        monkeypatch.setattr(settings, "REDIS_URL", test_redis_url)

        pool = create_redis_pool()
        try:
            client = redis.Redis(connection_pool=pool)
            assert await client.ping() is True
        finally:
            await pool.disconnect()

    async def test_exhausted_pool_raises_a_clear_error(self, monkeypatch, test_redis_url):
        monkeypatch.setattr(settings, "REDIS_URL", test_redis_url)
        monkeypatch.setattr(settings, "REDIS_MAX_CONNECTIONS", 1)

        pool = create_redis_pool()
        try:
            held = await pool.get_connection()
            try:
                with pytest.raises(RedisConnectionError, match="Too many connections"):
                    await pool.get_connection()
            finally:
                await pool.release(held)
        finally:
            await pool.disconnect()

    async def test_disconnect_actually_closes_pooled_connections(self, monkeypatch, test_redis_url):
        monkeypatch.setattr(settings, "REDIS_URL", test_redis_url)

        pool = create_redis_pool()
        client = redis.Redis(connection_pool=pool)
        await client.ping()  # establishes a connection, then releases it to the pool
        held_connection = pool._available_connections[0]
        assert held_connection.is_connected is True

        await pool.disconnect()

        assert held_connection.is_connected is False


@pytest.mark.unit
class TestGetRedisDependency:
    """get_redis() must bind to the shared pool and never close it itself."""

    async def test_two_dependency_resolutions_share_the_pool_and_survive_close(
        self, monkeypatch, test_redis_url
    ):
        monkeypatch.setattr(settings, "REDIS_URL", test_redis_url)
        pool = create_redis_pool()

        app = FastAPI()
        app.state.redis_pool = pool

        @app.get("/ping")
        async def ping_route(
            request: Request,
            redis_client: redis.Redis = Depends(get_redis),  # type: ignore[type-arg]
        ):
            await redis_client.ping()
            return {
                "client_pool_id": id(redis_client.connection_pool),
                "state_pool_id": id(request.app.state.redis_pool),
            }

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = await client.get("/ping")
                second = await client.get("/ping")

            assert first.status_code == 200
            assert second.status_code == 200
            assert first.json()["client_pool_id"] == first.json()["state_pool_id"]
            assert first.json()["client_pool_id"] == second.json()["client_pool_id"]

            # get_redis() closes its per-request client in `finally`; that must
            # not tear down the pool other requests (or workers) still share.
            survivor = redis.Redis(connection_pool=pool)
            assert await survivor.ping() is True
        finally:
            await pool.disconnect()

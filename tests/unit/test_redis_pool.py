"""Tests for the shared per-process Redis connection pool (#381).

Before this, app.core.redis.get_redis called redis.from_url(...) on every
dependency resolution and closed the client in `finally`, so every request
that depended on it paid a fresh TCP connect -- the dominant cause of the
"Too many open files" errors during the 2026-09-05 scraper waves. Now
app/main.py's lifespan creates one redis.asyncio.ConnectionPool per process
(via create_redis_pool), stores it on app.state.redis_pool, and get_redis
binds a lightweight client to that shared pool instead of opening its own.
"""

import asyncio
import socket
import time

import pytest
import redis.asyncio as redis
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import settings
from app.core.redis import RedisClient, create_redis_pool


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
            redis_client: RedisClient,
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


@pytest.mark.unit
class TestPoolTimeouts:
    """A dead or wedged Redis must fail fast, not stall for the kernel's TCP timeout."""

    async def test_silent_server_times_out_a_command(self, monkeypatch):
        async def accept_and_stay_silent(reader, writer):
            await reader.read()  # never replies; returns when the client hangs up
            writer.close()

        server = await asyncio.start_server(accept_and_stay_silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(settings, "REDIS_URL", f"redis://127.0.0.1:{port}/0")
        monkeypatch.setattr(settings, "REDIS_SOCKET_TIMEOUT", 0.2)

        pool = create_redis_pool()
        client = redis.Redis(connection_pool=pool)
        try:
            started = time.monotonic()
            with pytest.raises(RedisTimeoutError):
                await asyncio.wait_for(client.ping(), timeout=10)
            assert time.monotonic() - started < 5
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await pool.disconnect()
            server.close()
            await server.wait_closed()

    async def test_unanswered_connect_times_out(self, monkeypatch):
        # A listener with backlog 0 and no accept() loop: once its queue is full
        # the kernel drops further SYNs, so a new connect hangs like a dead host.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(0)
        port = listener.getsockname()[1]
        fillers = []
        for _ in range(3):
            filler = socket.socket()
            filler.setblocking(False)
            filler.connect_ex(("127.0.0.1", port))
            fillers.append(filler)
        await asyncio.sleep(0.2)

        monkeypatch.setattr(settings, "REDIS_URL", f"redis://127.0.0.1:{port}/0")
        monkeypatch.setattr(settings, "REDIS_SOCKET_CONNECT_TIMEOUT", 0.2)

        pool = create_redis_pool()
        client = redis.Redis(connection_pool=pool)
        try:
            started = time.monotonic()
            with pytest.raises(RedisTimeoutError):
                await asyncio.wait_for(client.ping(), timeout=10)
            assert time.monotonic() - started < 5
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await pool.disconnect()
            for filler in fillers:
                filler.close()
            listener.close()

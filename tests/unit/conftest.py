"""Fixtures for unit tests."""

import pytest
import redis.asyncio as redis


@pytest.fixture
async def redis_client():
    """
    Real Redis client for unit tests requiring Redis functionality.

    Connects to test Redis instance (same as integration tests).
    """
    # Use a dedicated Redis DB for tests to avoid interfering with dev services
    client = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)

    # Verify connection - skip test if Redis is unavailable
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"Redis not available at localhost:6379/15: {exc}")

    yield client

    # Cleanup - flush test database
    await client.flushdb()
    await client.aclose()

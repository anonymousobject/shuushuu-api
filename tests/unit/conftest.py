"""Fixtures for unit tests."""

import pytest
import redis.asyncio as redis


@pytest.fixture
async def redis_client():
    """
    Real Redis client for unit tests requiring Redis functionality.

    Connects to test Redis instance (same as integration tests).
    """
    client = redis.Redis(host="localhost", port=6379, db=1, decode_responses=False)

    # Verify connection
    await client.ping()

    yield client

    # Cleanup - flush test database
    await client.flushdb()
    await client.aclose()
